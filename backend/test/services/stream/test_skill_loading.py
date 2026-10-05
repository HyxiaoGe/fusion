import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.section_ids import SKILLS_CATALOG
from app.ai.skills import discover_skills
from app.schemas.chat import Usage
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_DEFINITIONS
from app.services.stream.agent_loop_driver import _run_round
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.limit_summary import remove_conflicting_tool_usage_contract
from app.services.stream.skill_loading import (
    LOAD_SKILL_TOOL_NAME,
    MAX_SKILL_LOADS_PER_RUN,
    LoadSkillHandler,
    SkillSession,
    build_load_skill_schema,
    build_skill_session,
)
from app.services.stream.step_lifecycle import AgentStepContext
from test.services.stream.test_agent_loop_driver import _runtime, _tool_definition, _tool_names

CAPABILITIES = {"functionCalling": True, "searchCapable": True}


def _write_skill(root: Path, name: str, tools: str = "", body: str = "Follow these steps.\n") -> None:
    directory = root / name
    directory.mkdir()
    metadata = f"metadata:\n  fusion-tools: {tools}\n" if tools else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for {name} tasks.\n{metadata}---\n\n{body}",
        encoding="utf-8",
    )


class _SkillRootTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        _write_skill(self.root, "web-research", "web_search url_read", body="Search, read, stop when enough.\n")
        _write_skill(self.root, "trip-planning", "weather_forecast route_compare")
        _write_skill(self.root, "comparison-advice")
        references = self.root / "trip-planning" / "references"
        references.mkdir()
        (references / "checklist.md").write_text("Packing checklist\n", encoding="utf-8")
        entries = discover_skills(self.root)
        patcher = patch("app.services.stream.skill_loading.discover_skills", return_value=entries)
        patcher.start()
        self.addCleanup(patcher.stop)


class SkillSessionTests(_SkillRootTestCase):
    def test_catalog_lists_only_skills_whose_tools_are_authorized(self):
        session = build_skill_session(["web_search"])

        self.assertEqual(set(session.entries), {"web-research", "comparison-advice"})
        prompt = session.catalog_prompt()
        self.assertIn("load_skill", prompt)
        self.assertIn("- web-research: Use for web-research tasks.", prompt)
        self.assertNotIn("trip-planning", prompt)
        schema = build_load_skill_schema(session)
        self.assertEqual(schema["function"]["name"], LOAD_SKILL_TOOL_NAME)
        self.assertEqual(
            schema["function"]["parameters"]["properties"]["name"]["enum"],
            ["comparison-advice", "web-research"],
        )

    async def test_handler_loads_body_once_then_reports_already_loaded(self):
        session = build_skill_session(["weather_forecast"])
        handler = LoadSkillHandler(session)

        first = await handler.execute({"name": "trip-planning"})
        again = await handler.execute({"name": "trip-planning"})
        reference = await handler.execute({"name": "trip-planning", "file": "references/checklist.md"})

        self.assertEqual(first.status, "success")
        context = handler.format_llm_context(first)
        self.assertTrue(context.startswith('<skill name="trip-planning">\nFollow these steps.'))
        self.assertIn("Reference files: references/checklist.md", context)
        self.assertTrue(again.data["already_loaded"])
        self.assertNotIn("content", again.data)
        self.assertIn("already loaded", handler.format_llm_context(again))
        self.assertEqual(reference.data["content"], "Packing checklist\n")
        self.assertIsNone(handler.build_content_block(first, "block", "log"))

    async def test_handler_rejects_unknown_skill_unlisted_file_and_stops_at_run_limit(self):
        session = build_skill_session(["web_search"])
        handler = LoadSkillHandler(session)

        unknown = await handler.execute({"name": "trip-planning"})
        escaped = await handler.execute({"name": "web-research", "file": "../trip-planning/SKILL.md"})

        self.assertEqual(unknown.data["reason"], "unknown_skill")
        self.assertIn("comparison-advice", handler.format_llm_context(unknown))
        self.assertEqual(escaped.data["reason"], "skill_file_unavailable")
        self.assertFalse(await handler.is_run_budget_exhausted())
        for _ in range(MAX_SKILL_LOADS_PER_RUN - session.attempts):
            await handler.execute({"name": "web-research"})
        self.assertTrue(await handler.is_run_budget_exhausted())
        limited = await handler.execute({"name": "comparison-advice"})
        self.assertEqual(limited.data["reason"], "skill_load_limit_reached")
        self.assertNotIn("comparison-advice", session.loaded)


class SkillCallConfigTests(_SkillRootTestCase):
    def test_skills_join_tools_and_catalog_follows_route(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities=CAPABILITIES,
        )

        tools = {tool["function"]["name"]: tool for tool in config.call_kwargs["tools"]}
        self.assertIn(LOAD_SKILL_TOOL_NAME, tools)
        self.assertNotIn("_plan_item_id", tools["web_search"]["function"]["parameters"]["properties"])
        self.assertNotIn(LOAD_SKILL_TOOL_NAME, config.announced_tools)
        self.assertIsInstance(config.dynamic_tool_handlers[LOAD_SKILL_TOOL_NAME], LoadSkillHandler)
        self.assertEqual(config.skill_tool_names, frozenset({LOAD_SKILL_TOOL_NAME}))
        self.assertEqual(config.unplanned_tool_names, frozenset({LOAD_SKILL_TOOL_NAME}))
        self.assertEqual(set(config.skill_session.entries), {"web-research", "comparison-advice"})

    def test_no_skills_when_tools_disabled_or_deep_research(self):
        disabled = build_agent_loop_call_config(
            provider="openai",
            options={"disable_tools": True},
            capabilities=CAPABILITIES,
        )
        research = build_agent_loop_call_config(
            provider="openai",
            options={"task_mode": "deep_research"},
            capabilities=CAPABILITIES,
        )

        for config in (disabled, research):
            self.assertIsNone(config.skill_session)
            self.assertNotIn(LOAD_SKILL_TOOL_NAME, config.dynamic_tool_handlers)
            self.assertNotIn(
                LOAD_SKILL_TOOL_NAME,
                [tool["function"]["name"] for tool in config.call_kwargs.get("tools", [])],
            )

    def test_product_tools_and_skills_are_announced_together(self):
        tools = [*AMAP_PRODUCT_DEFINITIONS, *FLYAI_TRAVEL_DEFINITIONS]
        handlers = {tool["function"]["name"]: object() for tool in tools}

        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities=CAPABILITIES,
            additional_tools=tools,
            dynamic_tool_handlers=handlers,
        )

        self.assertEqual(config.capability_resolution.package_id, "agent")
        self.assertIn("comparison-advice", config.skill_session.entries)
        self.assertIn(LOAD_SKILL_TOOL_NAME, _tool_names(config.call_kwargs))

    def test_final_synthesis_drops_skills_catalog(self):
        session = build_skill_session(["web_search"])
        messages = [
            {"role": "system", "content": session.catalog_prompt(), "section_id": SKILLS_CATALOG},
            {"role": "user", "content": "总结一下"},
        ]
        prompt_messages = [PromptMessage(**message) for message in messages]
        remove_conflicting_tool_usage_contract(prompt_messages, final_synthesis=True)

        self.assertEqual([message.role for message in prompt_messages], ["user"])


class SkillPlanStageTests(unittest.IsolatedAsyncioTestCase):
    async def _offered(self, coordinator: PlanCoordinator, *, skill_tool_names=frozenset({LOAD_SKILL_TOOL_NAME})):
        captured = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs["call_kwargs"])
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=1, output_tokens=1),
                output_deferred=kwargs.get("defer_output", False),
            )

        await _run_round(
            messages=[{"role": "user", "content": "帮我规划杭州两日游"}],
            state=AgentLoopState(plan_coordinator=coordinator),
            runtime=_runtime(
                plan_mode="on",
                skill_tool_names=skill_tool_names,
                call_kwargs={
                    "tools": [
                        _tool_definition("web_search"),
                        _tool_definition("update_plan"),
                        _tool_definition(LOAD_SKILL_TOOL_NAME),
                    ],
                    "tool_choice": "auto",
                },
                run_round_fn=run_round_fn,
            ),
            step_number=1,
            step_context=AgentStepContext(
                step_id="step-skill",
                step_number=1,
                started_at=1.0,
                thinking_block_id="thinking-skill",
                text_block_id="text-skill",
            ),
        )
        return captured[0]

    async def test_plan_mode_offers_skill_and_tools_without_forcing_update_plan(self):
        call_kwargs = await self._offered(PlanCoordinator(run_id="run-skill-plan", mode="on"))

        self.assertEqual(sorted(_tool_names(call_kwargs)), [LOAD_SKILL_TOOL_NAME, "update_plan", "web_search"])
        self.assertEqual(call_kwargs["tool_choice"], "auto")


def test_session_budget_property():
    session = SkillSession(entries={}, attempts=MAX_SKILL_LOADS_PER_RUN)
    assert session.budget_exhausted


@pytest.mark.bundled_skills
def test_bundled_skills_follow_the_announced_tools():
    config = build_agent_loop_call_config(
        provider="openai",
        options={},
        capabilities=CAPABILITIES,
    )

    # 每个内置 Skill 都按本 Run 公告的工具决定是否列出。
    authorized = frozenset(config.announced_tools)
    expected = {entry.name for entry in discover_skills() if entry.available_for(authorized)}
    assert {"comparison-advice", "web-research"} <= expected
    assert set(config.skill_session.entries) == expected
