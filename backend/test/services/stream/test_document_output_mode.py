"""文档交付：工具公告、计划门禁豁免、上下文注入。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.schemas.trajectory import TrajectoryCapabilityResolution
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.documents.agent_tools import DocumentToolSet, render_current_documents_context
from app.services.documents.service import DocumentVersionSnapshot
from app.services.stream.agent_loop_request_prep import (
    DOCUMENT_OUTPUT_MAX_TOKENS,
    build_agent_loop_call_config,
    prepare_agent_loop_messages,
)
from app.services.stream.plan_control import process_plan_control_calls
from app.services.stream.run_capability_router import serialize_capability_resolution

CAPABILITIES = {"functionCalling": True, "searchCapable": True}


def _snapshot(content: str = "# 香港三天两夜\n\n## D1\n- 油麻地") -> DocumentVersionSnapshot:
    return DocumentVersionSnapshot(
        document_id="doc-1",
        version=2,
        title="香港三天两夜攻略",
        format="markdown",
        content=content,
        change_summary=None,
        sources=(),
        created_at=None,
    )


class _Handler:
    def __init__(self, name: str):
        self.tool_name = name


def _tool_set(existing=()) -> DocumentToolSet:
    return DocumentToolSet(
        handlers={"create_document": _Handler("create_document"), "edit_document": _Handler("edit_document")},
        existing_documents=tuple(existing),
    )


def _tool_names(config) -> list[str]:
    return [tool["function"]["name"] for tool in config.call_kwargs.get("tools", [])]


class DocumentCallConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_mode_announces_document_tools_as_output_tools(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities=CAPABILITIES,
            document_tools=_tool_set([_snapshot()]),
        )

        resolution = config.capability_resolution
        self.assertEqual(resolution.package_id, "agent")
        self.assertNotIn("create_document", resolution.external_tool_names)
        self.assertEqual(config.announced_tools, list(resolution.external_tool_names))
        self.assertEqual(config.output_tool_names, frozenset({"create_document", "edit_document"}))
        self.assertIn("create_document", config.dynamic_tool_handlers)
        tools = {tool["function"]["name"]: tool for tool in config.call_kwargs["tools"]}
        self.assertIn("edit_document", tools)
        self.assertEqual(config.call_kwargs["max_tokens"], DOCUMENT_OUTPUT_MAX_TOKENS)
        self.assertIn("doc-1", config.document_context)
        TrajectoryCapabilityResolution.model_validate(
            {**serialize_capability_resolution(resolution), "bundle_fingerprint": "sha256:" + "0" * 64}
        )

    def test_document_tools_need_agent_mode_and_dynamic_tool_support(self):
        for capabilities, options in (
            ({"functionCalling": True, "agentTools": False, "searchCapable": True}, {}),
            (CAPABILITIES, {"disable_tools": True}),
            (CAPABILITIES, {"task_mode": "deep_research"}),
        ):
            with self.subTest(capabilities=capabilities, options=options):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options=options,
                    capabilities=capabilities,
                    document_tools=_tool_set(),
                )
                self.assertEqual(config.output_tool_names, frozenset())
                self.assertNotIn("create_document", _tool_names(config))
                self.assertNotIn("max_tokens", config.call_kwargs)

    async def test_document_contract_and_current_documents_enter_system_prompt(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities=CAPABILITIES,
            document_tools=_tool_set([_snapshot()]),
        )
        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=[],
            has_vision=False,
            file_ids=None,
            original_message="第二天换成迪士尼",
            call_config=config,
            file_repo_factory=lambda _db: object(),
            load_user_system_prompt_fn=lambda _db, _uid: None,
            preprocess_user_input=False,
        )
        sections = {section["section_id"]: section["content"] for section in prepared.prompt_snapshot["sections"]}
        self.assertIn("create_document", sections["document_output_contract"])
        self.assertIn('document_id="doc-1"', sections["current_documents"])
        self.assertIn("- 油麻地", sections["current_documents"])

    def test_current_documents_context_keeps_latest_document_whole_and_trims_older_ones(self):
        latest = _snapshot("A" * 59_000)
        older = DocumentVersionSnapshot(
            document_id="doc-0",
            version=1,
            title="旧文档",
            format="markdown",
            content="B" * 5_000,
            change_summary=None,
            sources=(),
            created_at=None,
        )
        context = render_current_documents_context((latest, older))
        self.assertIn("A" * 59_000, context)
        self.assertNotIn("B" * 5_000, context)
        self.assertIn('document_id="doc-0"', context)
        self.assertIsNone(render_current_documents_context(()))


class DocumentPlanControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_tools_execute_without_plan_binding_in_on_mode(self):
        coordinator = PlanCoordinator(run_id="run-doc", mode="on")
        accepted = coordinator.apply_model_update(
            {
                "reason": "先查天气再写攻略",
                "items": [
                    {
                        "id": "weather",
                        "title": "查询天气",
                        "status": "completed",
                        "kind": "search",
                        "depends_on": [],
                        "planned_tools": ["weather_forecast"],
                    },
                    {
                        "id": "answer",
                        "title": "写攻略",
                        "status": "pending",
                        "kind": "answer",
                        "depends_on": ["weather"],
                        "planned_tools": [],
                    },
                ],
            }
        )
        self.assertTrue(accepted.accepted, accepted.reason)

        result = await process_plan_control_calls(
            tool_calls=[{"id": "call-doc", "name": "create_document", "arguments": {"title": "t", "content": "c"}}],
            coordinator=coordinator,
            emitter=AsyncMock(),
        )

        self.assertEqual([call["id"] for call in result.external_tool_calls], ["call-doc"])
        self.assertNotIn("call-doc", result.tool_responses)


class DocumentToolWiringTests(unittest.TestCase):
    def _inputs(self, *, capabilities, options=None, loader):
        from app.services.stream.agent_loop_wiring import AgentLoopRunInput, prepare_agent_loop_call_config_inputs

        warnings: list[str] = []
        run_input = AgentLoopRunInput(
            conversation_id="conv-1",
            user_id="user-1",
            model_id="deepseek-chat",
            litellm_model="openai/deepseek-chat",
            litellm_kwargs={},
            provider="deepseek",
            raw_messages=[],
            has_vision=False,
            file_ids=None,
            original_message="出一份攻略",
            assistant_message_id="msg-1",
            task_id="task-1",
            options=options or {},
            capabilities=capabilities,
            trace_id="run-1",
        )
        dependencies = SimpleNamespace(
            load_dynamic_tools_fn=None,
            load_document_tools_fn=loader,
            warning_fn=warnings.append,
        )
        return prepare_agent_loop_call_config_inputs(run_input=run_input, db="db", dependencies=dependencies), warnings

    def test_loader_binds_run_identity_only_for_tool_capable_runs(self):
        calls = []

        def loader(db, **kwargs):
            calls.append((db, kwargs))
            return "tool-set"

        inputs, _ = self._inputs(capabilities=CAPABILITIES, loader=loader)
        self.assertEqual(inputs.document_tools, "tool-set")
        self.assertEqual(
            calls,
            [("db", {"conversation_id": "conv-1", "user_id": "user-1", "message_id": "msg-1", "run_id": "run-1"})],
        )
        for capabilities, options in (({"functionCalling": False}, {}), (CAPABILITIES, {"knowledge_grounded": True})):
            inputs, _ = self._inputs(capabilities=capabilities, options=options, loader=loader)
            self.assertIsNone(inputs.document_tools)
        self.assertEqual(len(calls), 1)

    def test_loader_failure_falls_back_to_chat_delivery(self):
        def loader(_db, **_kwargs):
            raise RuntimeError("db down")

        inputs, warnings = self._inputs(capabilities=CAPABILITIES, loader=loader)
        self.assertIsNone(inputs.document_tools)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("db down", warnings[0])
