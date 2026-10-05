"""文档交付形态：分类器字段、路由冻结、工具公告与计划门禁豁免、上下文注入。"""

import json
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
from app.services.stream.run_capability_model_classifier import _build_messages, _parse_model_route
from app.services.stream.run_capability_router import _CandidateRoute, serialize_capability_resolution
from app.utils.run_capability_contract import CAPABILITY_PACKAGES

CAPABILITIES = {"functionCalling": True, "searchCapable": True}


def _candidate(package_id: str, *, output_mode: str = "chat", **kwargs) -> _CandidateRoute:
    spec = CAPABILITY_PACKAGES[package_id]
    return _CandidateRoute(
        package_id,
        spec.confidence_options[0],
        spec.reason_code_options[0],
        True,
        resolution_mode=spec.resolution_mode,
        output_mode=output_mode,
        **kwargs,
    )


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


def _response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


def _tool_names(config) -> list[str]:
    return [tool["function"]["name"] for tool in config.call_kwargs.get("tools", [])]


class ClassifierOutputModeTests(unittest.TestCase):
    def _parse(self, **extra):
        payload = {
            "package_id": "direct",
            "explicit_tool_names": [],
            "network_policy": "allow",
            "denied_tool_names": [],
            **extra,
        }
        return _parse_model_route(_response(payload), [], include_current_date=True)

    def test_output_mode_is_model_decided_and_unknown_values_fall_back_to_chat(self):
        self.assertEqual(self._parse(output_mode="document").output_mode, "document")
        self.assertEqual(self._parse(output_mode="report").output_mode, "chat")
        self.assertEqual(self._parse().output_mode, "chat")

    def test_existing_document_titles_are_listed_for_revision_requests(self):
        messages = _build_messages(
            "第二天换成迪士尼",
            [],
            None,
            existing_document_titles=("香港三天两夜攻略",),
            token_counter_fn=lambda **_: 1,
        )
        self.assertIn('Existing documents in this conversation: ["香港三天两夜攻略"]', messages[0]["content"])
        self.assertIn("output_mode", messages[0]["content"])


class DocumentCallConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_document_mode_announces_document_tools_outside_capability_package(self):
        seen = {}

        def classify(**kwargs):
            seen.update(kwargs)
            return _candidate("fresh_web", output_mode="document")

        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities=CAPABILITIES,
            original_message="出一份10/16-18香港三天两夜攻略",
            classify_fn=classify,
            document_tools=_tool_set([_snapshot()]),
        )

        self.assertEqual(seen["existing_document_titles"], ("香港三天两夜攻略",))
        resolution = config.capability_resolution
        self.assertEqual(resolution.output_mode, "document")
        self.assertNotIn("create_document", resolution.external_tool_names)
        self.assertEqual(config.announced_tools, list(resolution.external_tool_names))
        self.assertEqual(config.output_tool_names, frozenset({"create_document", "edit_document"}))
        self.assertIn("create_document", config.dynamic_tool_handlers)
        tools = {tool["function"]["name"]: tool for tool in config.call_kwargs["tools"]}
        self.assertIn("edit_document", tools)
        self.assertEqual(config.call_kwargs["max_tokens"], DOCUMENT_OUTPUT_MAX_TOKENS)
        self.assertIn("doc-1", config.document_context)

        payload = serialize_capability_resolution(resolution)
        self.assertEqual(payload["output_mode"], "document")
        validated = TrajectoryCapabilityResolution.model_validate(
            {**payload, "bundle_fingerprint": "sha256:" + "0" * 64}
        )
        self.assertEqual(validated.model_dump()["output_mode"], "document")

    def test_chat_mode_and_unsupported_models_never_expose_document_tools(self):
        chat = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities=CAPABILITIES,
            original_message="你好",
            classify_fn=lambda **_: _candidate("direct"),
            document_tools=_tool_set(),
        )
        self.assertNotIn("create_document", _tool_names(chat))
        self.assertNotIn("output_mode", serialize_capability_resolution(chat.capability_resolution))
        self.assertNotIn(
            "output_mode",
            TrajectoryCapabilityResolution.model_validate(
                {
                    **serialize_capability_resolution(chat.capability_resolution),
                    "bundle_fingerprint": "sha256:" + "0" * 64,
                }
            ).model_dump(),
        )

        for capabilities, options in (
            ({"functionCalling": True, "agentTools": False, "searchCapable": True}, {}),
            (CAPABILITIES, {"disable_tools": True}),
        ):
            config = build_agent_loop_call_config(
                provider="openai",
                options=options,
                capabilities=capabilities,
                original_message="写一份周报",
                classify_fn=lambda **_: _candidate("direct", output_mode="document"),
                document_tools=_tool_set(),
            )
            self.assertEqual(config.output_tool_names, frozenset())
            self.assertNotIn("create_document", _tool_names(config))
            self.assertNotIn("max_tokens", config.call_kwargs)

    def test_clarification_route_cannot_enter_document_mode(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities=CAPABILITIES,
            original_message="帮我做个攻略",
            classify_fn=lambda **_: _candidate("clarification_only", output_mode="document"),
            document_tools=_tool_set(),
        )
        self.assertEqual(config.capability_resolution.output_mode, "chat")
        self.assertEqual(config.output_tool_names, frozenset())

    async def test_document_contract_and_current_documents_enter_system_prompt(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities=CAPABILITIES,
            original_message="第二天换成迪士尼",
            classify_fn=lambda **_: _candidate("direct", output_mode="document"),
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
            load_authorized_tool_names_fn=None,
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
