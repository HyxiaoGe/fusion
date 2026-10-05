import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from app.ai.prompts.prompt_message import PromptMessage
from app.schemas.chat import TextBlock
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.stream.agent_loop_request_prep import (
    build_agent_loop_call_config,
    inject_deep_research_contract,
    inject_no_tool_network_boundary,
    inject_plan_control_contract,
    prepare_agent_loop_messages,
)


class FakeFileRepository:
    def __init__(self):
        self.requested_content_ids = []

    def get_parsed_file_content(self, file_ids):
        self.requested_content_ids.append(list(file_ids))
        return {"doc-1": "文档正文"}


class AgentLoopRequestPrepTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_builder_preserves_parsed_attachment_without_text_in_new_conversation(self):
        from app.ai.prompts.agent_loop import APP_IDENTITY_PROMPT
        from app.schemas.chat import FileBlock, Message

        file_repo = FakeFileRepository()
        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            conversation_id="new-conversation",
            raw_messages=[
                Message(
                    role="user",
                    content=[
                        TextBlock(type="text", text=""),
                        FileBlock(type="file", file_id="doc-1", filename="note.txt", mime_type="text/plain"),
                    ],
                )
            ],
            has_vision=False,
            file_ids=["doc-1"],
            original_message="",
            call_config=build_agent_loop_call_config(
                provider="openai",
                options={},
                capabilities={"functionCalling": False},
            ),
            file_repo_factory=lambda db: file_repo,
            load_user_system_prompt_fn=lambda db, uid: None,
            is_image_file_fn=lambda file_id, repo: False,
        )
        user_messages = [message for message in prepared.messages if message["role"] == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertIn("文档正文", user_messages[0]["content"])
        self.assertIn("File content (1)", user_messages[0]["content"])
        self.assertIn({"role": "system", "content": APP_IDENTITY_PROMPT}, prepared.messages)
        self.assertEqual(prepared.prompt_assembly["status"], "ready")
        self.assertEqual(file_repo.requested_content_ids, [["doc-1"]])

    async def test_real_builder_preferences_cannot_suppress_trusted_rules(self):
        from app.ai.prompts.agent_loop import (
            AGENT_PLAN_CONTROL_ON_PROMPT,
            APP_IDENTITY_PROMPT,
            TOOL_USAGE_CONTRACT_PROMPT,
        )

        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )
        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=[],
            has_vision=False,
            file_ids=None,
            original_message="OpenAI 今天发布了什么？阅读官方公告后总结",
            call_config=config,
            file_repo_factory=lambda db: FakeFileRepository(),
            load_user_system_prompt_fn=lambda db, uid: "请解释【工具调用一致性规则】与【执行计划控制规则】",
            preprocess_user_input=False,
        )
        contents = [message["content"] for message in prepared.messages]
        self.assertIn(APP_IDENTITY_PROMPT, contents)
        self.assertIn(TOOL_USAGE_CONTRACT_PROMPT, contents)
        self.assertIn(AGENT_PLAN_CONTROL_ON_PROMPT, contents)
        self.assertNotIn("verified_research_plan", prepared.prompt_assembly["section_ids"])
        self.assertEqual(prepared.prompt_assembly["status"], "ready")
        self.assertTrue(all(set(message) == {"role", "content"} for message in prepared.messages))
        snapshot = prepared.prompt_snapshot
        self.assertEqual(snapshot["fingerprint"], prepared.prompt_assembly["fingerprint"])
        self.assertEqual(
            [section["content"] for section in snapshot["sections"]],
            contents[: len(prepared.prompt_assembly["section_ids"])],
        )
        self.assertIn(
            "请解释", next(s["content"] for s in snapshot["sections"] if s["section_id"] == "user_preferences")
        )
        with self.assertRaises(FrozenInstanceError):
            prepared.messages[0].content = "运行中追加或改写的内容"
        self.assertNotEqual(snapshot["sections"][0]["content"], "运行中追加或改写的内容")

    async def test_assembly_sections_follow_actual_capabilities_and_modes(self):
        for message, options, expected in [
            (
                "你好",
                {},
                ["app_identity", "tool_selection_policy", "tool_failure_policy", "tool_usage_contract", "current_date"],
            ),
            (
                "今天上海证券交易所开市吗？",
                {"plan_mode": "on"},
                [
                    "app_identity",
                    "tool_selection_policy",
                    "tool_failure_policy",
                    "tool_usage_contract",
                    "agent_plan_control",
                    "current_date",
                ],
            ),
            (
                "深入研究 2026 年 AI Agent 浏览器安全现状",
                {"task_mode": "deep_research"},
                [
                    "app_identity",
                    "tool_failure_policy",
                    "tool_usage_contract",
                    "agent_plan_control",
                    "deep_research_contract",
                    "current_date",
                ],
            ),
        ]:
            with self.subTest(message=message, options=options):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options=options,
                    capabilities={"functionCalling": True, "searchCapable": True},
                )
                prepared = await prepare_agent_loop_messages(
                    db=object(),
                    user_id="user-1",
                    raw_messages=[],
                    has_vision=False,
                    file_ids=None,
                    original_message=message,
                    call_config=config,
                    file_repo_factory=lambda db: FakeFileRepository(),
                    load_user_system_prompt_fn=lambda db, uid: None,
                    preprocess_user_input=False,
                )
                self.assertEqual(prepared.prompt_assembly["section_ids"], expected)
                self.assertEqual(len(prepared.messages), len(expected))
                self.assertEqual([s["section_id"] for s in prepared.prompt_snapshot["sections"]], expected)
                self.assertTrue(all(s["content"] for s in prepared.prompt_snapshot["sections"]))

    def test_provider_reasoning_adaptation_runs_after_route_tool_materialization(self):
        cases = [
            (
                "deepseek",
                "今天上海证券交易所开市吗？",
                ["web_search", "url_read"],
                {"thinking": {"type": "enabled"}},
                None,
            ),
            (
                "volcengine",
                "今天上海证券交易所开市吗？",
                ["web_search", "url_read"],
                {"thinking": {"type": "disabled"}},
                None,
            ),
            (
                "gemini",
                "你好",
                ["web_search", "url_read"],
                None,
                "high",
            ),
        ]

        for provider, message, expected_tools, expected_extra_body, expected_reasoning_effort in cases:
            with self.subTest(provider=provider, message=message):
                config = build_agent_loop_call_config(
                    provider=provider,
                    options={},
                    capabilities={
                        "functionCalling": True,
                        "searchCapable": True,
                        "agentTools": True,
                        "deepThinking": True,
                    },
                )

                self.assertEqual(config.announced_tools, expected_tools)
                self.assertEqual(config.call_kwargs.get("extra_body"), expected_extra_body)
                self.assertEqual(config.call_kwargs.get("reasoning_effort"), expected_reasoning_effort)
                if provider == "deepseek" and expected_tools:
                    self.assertNotIn("tool_choice", config.call_kwargs)

    async def test_io_failure_is_not_an_assembly_failure(self):
        def failed_preference_read(db, user_id):
            raise RuntimeError("数据库失败")

        with patch("app.ai.prompts.system_prompt.perf_counter") as timer:
            with self.assertRaisesRegex(RuntimeError, "数据库失败"):
                await prepare_agent_loop_messages(
                    db=object(),
                    user_id="user-1",
                    raw_messages=[],
                    has_vision=False,
                    file_ids=None,
                    original_message="测试",
                    call_config=build_agent_loop_call_config(
                        provider="openai",
                        options={},
                        capabilities={"functionCalling": False},
                    ),
                    file_repo_factory=lambda db: FakeFileRepository(),
                    load_user_system_prompt_fn=failed_preference_read,
                    preprocess_user_input=False,
                )
            timer.assert_not_called()

    def test_deep_research_only_announces_stage_executable_tools(self):
        handlers = {tool["function"]["name"]: object() for tool in AMAP_PRODUCT_DEFINITIONS}
        config = build_agent_loop_call_config(
            provider="openai",
            options={"task_mode": "deep_research"},
            capabilities={
                "functionCalling": True,
                "searchCapable": True,
                "agentTools": True,
            },
            additional_tools=AMAP_PRODUCT_DEFINITIONS,
            dynamic_tool_handlers=handlers,
        )

        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        self.assertNotIn("route_compare", config.announced_tools)

    def test_plan_mode_defaults_off_without_plan_control_tool(self):
        for options in ({}, {"plan_mode": "auto"}):
            with self.subTest(options=options):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options=options,
                    capabilities={"functionCalling": True, "searchCapable": True},
                )

                model_tool_names = [tool["function"]["name"] for tool in config.call_kwargs["tools"]]
                self.assertEqual(config.plan_mode, "off")
                self.assertNotIn("update_plan", model_tool_names)
                self.assertEqual(config.announced_tools, ["web_search", "url_read"])
                self.assertEqual(config.control_tool_names, frozenset())
                web_tool = next(
                    tool for tool in config.call_kwargs["tools"] if tool["function"]["name"] == "web_search"
                )
                self.assertNotIn("_plan_item_id", web_tool["function"]["parameters"]["properties"])

    def test_deep_research_forces_plan_mode_and_records_task_policy(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"task_mode": "deep_research", "plan_mode": "off"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        self.assertEqual(config.task_mode, "deep_research")
        self.assertEqual(config.plan_mode, "on")
        self.assertEqual(config.network_profile, "deep_research")
        self.assertEqual(config.evidence_policy, "deep_research_v1")
        tools = {tool["function"]["name"]: tool for tool in config.call_kwargs["tools"]}
        self.assertIn("url_read", tools)
        self.assertNotIn("_plan_item_id", tools["url_read"]["function"]["parameters"]["properties"])
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])

    def test_deep_research_contract_is_only_injected_for_research_mode(self):
        research = build_agent_loop_call_config(
            provider="openai",
            options={"task_mode": "deep_research"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )
        standard = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        research_messages = inject_deep_research_contract([{"role": "user", "content": "调研"}], research)
        standard_messages = inject_deep_research_contract([{"role": "user", "content": "调研"}], standard)

        self.assertIn("[Deep-research execution contract]", research_messages[0]["content"])
        self.assertIn("complementary queries", research_messages[0]["content"])
        self.assertIn("Use [n] citations in the answer body", research_messages[0]["content"])
        self.assertNotIn("planned_tools", research_messages[0]["content"])
        self.assertEqual(standard_messages, [{"role": "user", "content": "调研"}])

    def test_plan_mode_off_preserves_old_tools_without_control_tool(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        model_tool_names = [tool["function"]["name"] for tool in config.call_kwargs["tools"]]
        self.assertEqual(config.plan_mode, "off")
        self.assertNotIn("update_plan", model_tool_names)
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        parameters = config.call_kwargs["tools"][0]["function"]["parameters"]
        self.assertNotIn("_plan_item_id", parameters["properties"])

    def test_on_mode_plan_tool_is_display_only_and_external_tools_stay_unbound(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        tools = {tool["function"]["name"]: tool for tool in config.call_kwargs["tools"]}
        web_parameters = tools["web_search"]["function"]["parameters"]
        plan_parameters = tools["update_plan"]["function"]["parameters"]
        plan_item = plan_parameters["properties"]["plan"]["items"]

        self.assertNotIn("_plan_item_id", web_parameters["properties"])
        self.assertEqual(plan_item["required"], ["step", "status"])
        self.assertNotIn("enum", plan_item["properties"]["planned_tools"]["items"])
        self.assertEqual(plan_parameters["properties"]["plan"]["maxItems"], 14)

    def test_plan_mode_is_off_when_no_tool_is_available(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": False},
        )

        self.assertEqual(config.capability_resolution.package_id, "tools_unavailable")
        self.assertEqual(config.plan_mode, "off")
        self.assertNotIn("tools", config.call_kwargs)
        self.assertEqual(config.announced_tools, [])

    def test_requested_on_mode_defensively_disables_when_model_cannot_call_control_tool(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": False, "searchCapable": False},
        )

        self.assertEqual(config.plan_mode, "off")
        self.assertNotIn("tools", config.call_kwargs)
        self.assertEqual(config.control_tool_names, frozenset())

    def test_on_plan_contract_describes_plan_as_display_only(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        prepared = inject_plan_control_contract([{"role": "user", "content": "你好"}], config)

        self.assertIn("[Execution plan]", prepared[0]["content"])
        self.assertIn("does not restrict which tools you may call", prepared[0]["content"])

    def test_plan_contract_is_not_injected_when_plan_mode_is_off(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "searchCapable": True},
        )
        messages = [{"role": "user", "content": "规划通勤路线"}]

        self.assertIs(inject_plan_control_contract(messages, config), messages)

    def test_build_call_config_applies_controlled_max_tokens(self):
        for raw_value, expected in ((1, 1), (1024, 1024), (9999, 4096)):
            with self.subTest(raw_value=raw_value):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options={"max_tokens": raw_value},
                    capabilities={"functionCalling": False},
                )

                self.assertEqual(config.call_kwargs["max_tokens"], expected)

    def test_build_call_config_ignores_invalid_max_tokens(self):
        for raw_value in (True, False, 0, -1, 1.5, "1024", None):
            with self.subTest(raw_value=raw_value):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options={"max_tokens": raw_value},
                    capabilities={"functionCalling": False},
                )

                self.assertNotIn("max_tokens", config.call_kwargs)

    def test_build_call_config_can_disable_supported_tools(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"disable_tools": True},
            capabilities={"functionCalling": True, "searchCapable": True},
        )

        self.assertFalse(config.supports_function_calling)
        self.assertEqual(config.announced_tools, [])
        self.assertNotIn("tools", config.call_kwargs)
        self.assertNotIn("tool_choice", config.call_kwargs)

    def test_build_call_config_enables_tools_and_volcengine_reasoning_compat(self):
        config = build_agent_loop_call_config(
            provider="volcengine",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "searchCapable": True, "deepThinking": True},
        )

        self.assertTrue(config.should_use_reasoning)
        self.assertTrue(config.supports_function_calling)
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        self.assertEqual(config.call_kwargs["tool_choice"], "auto")
        self.assertEqual(config.call_kwargs["tools"][0]["function"]["name"], "web_search")
        self.assertEqual(config.call_kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_deepseek_plan_mode_enables_thinking_and_removes_incompatible_tool_choice(self):
        config = build_agent_loop_call_config(
            provider="deepseek",
            options={"plan_mode": "on"},
            capabilities={
                "functionCalling": True,
                "agentTools": True,
                "searchCapable": True,
                "deepThinking": True,
            },
        )

        self.assertTrue(config.should_use_reasoning)
        self.assertEqual(config.call_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertNotIn("tool_choice", config.call_kwargs)

    def test_moonshot_plan_mode_keeps_native_thinking_configuration(self):
        config = build_agent_loop_call_config(
            provider="moonshot",
            options={"plan_mode": "on"},
            capabilities={
                "functionCalling": True,
                "agentTools": True,
                "searchCapable": True,
                "deepThinking": True,
            },
        )

        self.assertTrue(config.should_use_reasoning)
        self.assertNotIn("extra_body", config.call_kwargs)

    def test_deepseek_non_plan_search_uses_explicit_thinking_protocol(self):
        config = build_agent_loop_call_config(
            provider="deepseek",
            options={"plan_mode": "off"},
            capabilities={
                "functionCalling": True,
                "agentTools": True,
                "searchCapable": True,
                "deepThinking": True,
            },
        )

        self.assertEqual(config.call_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertNotIn("tool_choice", config.call_kwargs)

    def test_gemini_reasoning_models_request_visible_thought_summaries(self):
        config = build_agent_loop_call_config(
            provider="gemini",
            options={"plan_mode": "on"},
            capabilities={
                "functionCalling": True,
                "agentTools": True,
                "searchCapable": True,
                "deepThinking": True,
            },
        )

        self.assertTrue(config.should_use_reasoning)
        self.assertEqual(config.call_kwargs["reasoning_effort"], "high")
        self.assertEqual(config.call_kwargs["tool_choice"], "auto")

    def test_build_call_config_respects_explicit_reasoning_override(self):
        config = build_agent_loop_call_config(
            provider="volcengine",
            options={"use_reasoning": False},
            capabilities={"functionCalling": True, "searchCapable": True, "deepThinking": True},
        )

        self.assertFalse(config.should_use_reasoning)
        self.assertTrue(config.supports_function_calling)
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        self.assertNotIn("extra_body", config.call_kwargs)

    def test_build_call_config_disables_agent_tools_when_agent_tools_capability_is_false(self):
        config = build_agent_loop_call_config(
            provider="qwen",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "agentTools": False, "deepThinking": False},
        )

        self.assertFalse(config.supports_function_calling)
        self.assertEqual(config.announced_tools, [])
        self.assertNotIn("tools", config.call_kwargs)
        self.assertNotIn("tool_choice", config.call_kwargs)

    def test_build_call_config_uses_search_capable_as_runtime_tool_contract(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "agentTools": False, "searchCapable": True},
        )

        self.assertTrue(config.supports_function_calling)
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        self.assertEqual(config.call_kwargs["tool_choice"], "auto")

    def test_build_call_config_disables_tools_when_search_capable_is_false(self):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "agentTools": True, "webSearch": True, "searchCapable": False},
        )

        self.assertFalse(config.supports_function_calling)
        self.assertEqual(config.announced_tools, [])
        self.assertNotIn("tools", config.call_kwargs)
        self.assertNotIn("tool_choice", config.call_kwargs)

    def test_build_call_config_injects_mcp_tools_for_function_calling_model_without_search(self):
        mcp_tool = {
            "type": "function",
            "function": {
                "name": "mcp_microsoft_docs_a1b2c3d4",
                "description": "搜索 Microsoft Learn 文档",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
        handler = object()
        binding = {"alias": "mcp_microsoft_docs_a1b2c3d4", "server_id": "server-1"}

        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "searchCapable": False},
            additional_tools=[mcp_tool],
            dynamic_tool_handlers={"mcp_microsoft_docs_a1b2c3d4": handler},
            tool_bindings=[binding],
        )

        self.assertFalse(config.supports_function_calling)
        self.assertTrue(config.supports_dynamic_tools)
        self.assertEqual(config.announced_tools, ["mcp_microsoft_docs_a1b2c3d4"])
        self.assertEqual(config.call_kwargs["tools"], [mcp_tool])
        self.assertEqual(config.call_kwargs["tool_choice"], "auto")
        self.assertIs(config.dynamic_tool_handlers["mcp_microsoft_docs_a1b2c3d4"], handler)
        self.assertEqual(config.tool_bindings, [binding])

    def test_build_call_config_injects_stable_amap_product_tool_without_false_network_boundary(self):
        product_tool = {
            "type": "function",
            "function": {
                "name": "local_place_search",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        }
        handler = object()

        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities={"functionCalling": True, "agentTools": True, "searchCapable": False},
            additional_tools=[product_tool],
            dynamic_tool_handlers={"local_place_search": handler},
            tool_bindings=[{"alias": "local_place_search", "server_id": "amap-1"}],
        )
        messages = [{"role": "user", "content": "搜索民治附近的咖啡店"}]

        self.assertEqual(config.announced_tools, ["local_place_search"])
        self.assertIs(config.dynamic_tool_handlers["local_place_search"], handler)
        self.assertIs(inject_no_tool_network_boundary(messages, config.call_kwargs), messages)

    def test_build_call_config_respects_explicit_agent_tools_capability_for_mcp(self):
        mcp_tool = {
            "type": "function",
            "function": {"name": "mcp_docs_a1b2c3d4", "parameters": {"type": "object"}},
        }

        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "off"},
            capabilities={"functionCalling": True, "agentTools": False, "searchCapable": False},
            additional_tools=[mcp_tool],
            dynamic_tool_handlers={"mcp_docs_a1b2c3d4": object()},
            tool_bindings=[{"alias": "mcp_docs_a1b2c3d4"}],
        )

        self.assertFalse(config.supports_dynamic_tools)
        self.assertEqual(config.dynamic_tool_handlers, {})
        self.assertEqual(config.tool_bindings, [])
        self.assertNotIn("tools", config.call_kwargs)

    def test_build_call_config_disable_tools_blocks_mcp_tools_too(self):
        mcp_tool = {
            "type": "function",
            "function": {"name": "mcp_docs_a1b2c3d4", "parameters": {"type": "object"}},
        }

        config = build_agent_loop_call_config(
            provider="openai",
            options={"disable_tools": True},
            capabilities={"functionCalling": True, "searchCapable": True},
            additional_tools=[mcp_tool],
            dynamic_tool_handlers={"mcp_docs_a1b2c3d4": object()},
            tool_bindings=[{"alias": "mcp_docs_a1b2c3d4"}],
        )

        self.assertFalse(config.supports_function_calling)
        self.assertFalse(config.supports_dynamic_tools)
        self.assertEqual(config.dynamic_tool_handlers, {})
        self.assertEqual(config.tool_bindings, [])
        self.assertNotIn("tools", config.call_kwargs)

    async def test_authorized_mcp_alias_degrades_atomically_when_execution_is_unavailable(self):
        alias = "mcp_docs_a1b2c3d4"
        mcp_tool = {
            "type": "function",
            "function": {
                "name": alias,
                "description": "搜索 Microsoft Learn 文档",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
        handler = object()
        binding = {"alias": alias, "server_id": "server-1"}
        message = f"请使用 {alias} 查询 Microsoft Learn"

        async def build_llm_messages_fn(
            _raw_messages,
            _has_vision,
            _repo,
            _user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            return [{"role": "user", "content": message}]

        for options, capabilities, expected_reason in (
            (
                {"disable_tools": True},
                {"functionCalling": True, "searchCapable": True},
                "tools_disabled",
            ),
            (
                {},
                {"functionCalling": False, "searchCapable": False},
                "function_calling_unavailable",
            ),
        ):
            with self.subTest(expected_reason=expected_reason):
                config = build_agent_loop_call_config(
                    provider="openai",
                    options=options,
                    capabilities=capabilities,
                    additional_tools=[mcp_tool],
                    dynamic_tool_handlers={alias: handler},
                    tool_bindings=[binding],
                )
                prepared = await prepare_agent_loop_messages(
                    db=object(),
                    user_id="user-1",
                    raw_messages=[],
                    has_vision=False,
                    file_ids=None,
                    original_message=message,
                    call_config=config,
                    file_repo_factory=lambda _db: object(),
                    load_user_system_prompt_fn=lambda _db, _user_id: None,
                    build_llm_messages_fn=build_llm_messages_fn,
                    preprocess_user_input=False,
                )

                self.assertEqual(config.capability_resolution.package_id, "tools_unavailable")
                self.assertEqual(config.capability_resolution.reason_codes, (expected_reason,))
                self.assertTrue(config.capability_resolution.network_boundary_required)
                self.assertEqual(config.capability_resolution.external_tool_names, ())
                self.assertNotIn("tools", config.call_kwargs)
                self.assertEqual(config.dynamic_tool_handlers, {})
                self.assertEqual(config.tool_bindings, [])
                self.assertEqual(config.announced_tools, [])
                self.assertEqual(prepared.final_tool_names, [])
                self.assertEqual(
                    prepared.prompt_assembly["section_ids"],
                    ["app_identity", "no_tool_network_boundary", "current_date"],
                )

        unauthorized = build_agent_loop_call_config(
            provider="openai",
            options={"disable_tools": True},
            capabilities={"functionCalling": True, "searchCapable": True},
            # 未授权别名不会出现在模型的可选工具里，模型不可能选中它，
            # 因此这里不注入候选：走分类失败兜底，再因工具被关闭降级。
        )
        self.assertEqual(unauthorized.capability_resolution.package_id, "tools_unavailable")
        self.assertEqual(unauthorized.capability_resolution.external_tool_names, ())
        self.assertTrue(unauthorized.capability_resolution.network_boundary_required)

    def test_authorized_mcp_alias_is_not_announced_when_agent_tools_are_unsupported(self):
        alias = "mcp_docs_a1b2c3d4"
        mcp_tool = {
            "type": "function",
            "function": {"name": alias, "description": "docs", "parameters": {"type": "object", "properties": {}}},
        }
        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities={"functionCalling": True, "searchCapable": True, "agentTools": False},
            additional_tools=[mcp_tool],
            dynamic_tool_handlers={alias: object()},
        )

        self.assertEqual(config.capability_resolution.package_id, "agent")
        self.assertEqual(config.announced_tools, ["web_search", "url_read"])
        self.assertNotIn(alias, [tool["function"]["name"] for tool in config.call_kwargs["tools"]])
        self.assertEqual(config.dynamic_tool_handlers, {})

    def test_mcp_tool_prevents_false_no_network_boundary(self):
        messages = [{"role": "user", "content": "查一下 Microsoft Learn"}]
        call_kwargs = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "mcp_microsoft_docs_a1b2c3d4", "parameters": {"type": "object"}},
                }
            ]
        }

        self.assertIs(inject_no_tool_network_boundary(messages, call_kwargs), messages)

    async def test_prepare_messages_injects_no_tool_network_boundary_when_agent_tools_disabled(self):
        async def build_llm_messages_fn(
            _raw_messages,
            _has_vision,
            _repo,
            _user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            return [
                {"role": "user", "content": "OpenAI 最近发布了什么模型？"},
            ]

        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=["raw"],
            has_vision=False,
            file_ids=None,
            original_message="OpenAI 最近发布了什么模型？",
            call_config=build_agent_loop_call_config(
                provider="qwen",
                options={},
                capabilities={"functionCalling": True, "agentTools": False},
            ),
            file_repo_factory=lambda _db: object(),
            load_user_system_prompt_fn=lambda _db, _user_id: None,
            build_llm_messages_fn=build_llm_messages_fn,
        )

        self.assertEqual(
            [message["role"] for message in prepared.messages],
            ["system", "system", "system", "user"],
        )
        self.assertIn("[Fusion identity consistency]", prepared.messages[0]["content"])
        self.assertIn("[No web-access tools]", prepared.messages[1]["content"])
        self.assertIn("Do not claim or imply that you searched", prepared.messages[1]["content"])
        self.assertIn("cannot verify them in real time", prepared.messages[1]["content"])
        self.assertIn("Never present existing knowledge as current verification", prepared.messages[1]["content"])
        self.assertIn("Answer ordinary stable questions directly", prepared.messages[1]["content"])
        self.assertNotIn("switch models", prepared.messages[1]["content"])
        self.assertNotIn("[Tool-call consistency]", prepared.messages[1]["content"])
        self.assertIn("[Current date]", prepared.messages[2]["content"])
        self.assertEqual(prepared.messages[3]["content"], "OpenAI 最近发布了什么模型？")

    async def test_prepare_messages_injects_no_vision_boundary_when_image_attached_to_text_model(self):
        async def build_llm_messages_fn(
            _raw_messages,
            _has_vision,
            _repo,
            _user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            return [
                {"role": "user", "content": "这张图里有什么？"},
            ]

        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=["raw"],
            has_vision=False,
            file_ids=["image-1"],
            original_message="这张图里有什么？",
            call_config=build_agent_loop_call_config(
                provider="qwen",
                options={},
                capabilities={"functionCalling": True, "agentTools": False, "vision": False},
            ),
            file_repo_factory=lambda _db: object(),
            load_user_system_prompt_fn=lambda _db, _user_id: None,
            build_llm_messages_fn=build_llm_messages_fn,
            is_image_file_fn=lambda file_id, _repo: file_id == "image-1",
        )

        self.assertEqual(
            [message["role"] for message in prepared.messages],
            ["system", "system", "system", "system", "user"],
        )
        self.assertIn("[No image-understanding capability]", prepared.messages[1]["content"])
        self.assertIn("cannot read or understand image attachments", prepared.messages[1]["content"])
        self.assertIn("Do not guess its contents", prepared.messages[1]["content"])
        self.assertEqual(prepared.messages[2].section_id, "no_tool_network_boundary")
        self.assertIn("[Current date]", prepared.messages[3]["content"])
        self.assertEqual(prepared.messages[4]["content"], "这张图里有什么？")

    async def test_prepare_messages_builds_llm_input_files_url_context_and_tool_contract(self):
        file_repo = FakeFileRepository()
        build_calls = []
        inject_calls = []

        async def build_llm_messages_fn(
            raw_messages,
            has_vision,
            repo,
            user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            build_calls.append(
                {
                    "raw_messages": raw_messages,
                    "has_vision": has_vision,
                    "repo": repo,
                    "user_system_prompt": user_system_prompt,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                }
            )
            return [
                {"role": "user", "content": "原始问题"},
            ]

        def inject_file_content_fn(messages, original_message, file_contents):
            inject_calls.append(
                {
                    "messages": list(messages),
                    "original_message": original_message,
                    "file_contents": file_contents,
                }
            )
            result = list(messages)
            result[-1] = {"role": "user", "content": f"{original_message}\n\n{file_contents['doc-1']}"}
            return result

        async def preprocess_url_in_message_fn(original_message, supports_function_calling, call_kwargs):
            self.assertEqual(original_message, "请阅读 https://example.com/a")
            self.assertTrue(supports_function_calling)
            self.assertEqual(
                [tool["function"]["name"] for tool in call_kwargs["tools"]],
                ["web_search", "url_read"],
            )
            return (
                TextBlock(type="text", id="url-block", text="URL 摘要"),
                {"role": "user", "content": "<web_context>网页正文</web_context>"},
                "https://example.com/a",
            )

        call_config = build_agent_loop_call_config(
            provider="openai",
            options={"use_reasoning": True},
            capabilities={"functionCalling": True, "searchCapable": True, "deepThinking": True},
        )

        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=["raw"],
            has_vision=False,
            file_ids=["doc-1", "image-1"],
            original_message="请阅读 https://example.com/a",
            call_config=call_config,
            file_repo_factory=lambda _db: file_repo,
            load_user_system_prompt_fn=lambda _db, _user_id: "用户偏好",
            build_llm_messages_fn=build_llm_messages_fn,
            is_image_file_fn=lambda file_id, _repo: file_id == "image-1",
            inject_file_content_fn=inject_file_content_fn,
            preprocess_url_in_message_fn=preprocess_url_in_message_fn,
        )

        self.assertIsNone(build_calls[0]["user_system_prompt"])
        self.assertIn("user's personalization preferences", prepared.messages[6]["content"])
        self.assertIn("用户偏好", prepared.messages[6]["content"])
        self.assertIs(build_calls[0]["repo"], file_repo)
        self.assertEqual(build_calls[0]["user_id"], "user-1")
        self.assertIsNone(build_calls[0]["conversation_id"])
        self.assertEqual(file_repo.requested_content_ids, [["doc-1"]])
        self.assertEqual(inject_calls[0]["file_contents"], {"doc-1": "文档正文"})
        self.assertEqual([block.id for block in prepared.initial_content_blocks], ["url-block"])
        self.assertEqual(prepared.final_tool_names, ["web_search", "url_read"])
        self.assertEqual(
            [message["role"] for message in prepared.messages],
            ["system", "system", "system", "system", "system", "system", "system", "user", "user"],
        )
        self.assertIn("[Fusion identity consistency]", prepared.messages[0]["content"])
        self.assertIn("[No image-understanding capability]", prepared.messages[1]["content"])
        self.assertIn("<web_context>", prepared.messages[7]["content"])
        self.assertIn("文档正文", prepared.messages[8]["content"])
        self.assertEqual(call_config.announced_tools, ["web_search", "url_read"])

    def test_tool_usage_contract_uses_centralized_prompt(self):
        from app.ai.prompts.agent_loop import NETWORK_DECISION_PROMPT, TOOL_USAGE_CONTRACT_PROMPT
        from app.services.stream.agent_loop_request_prep import inject_tool_usage_contract

        messages = [{"role": "user", "content": "OpenAI 最新公告"}]
        call_kwargs = {"tools": [{"type": "function", "function": {"name": "web_search"}}]}

        prepared = inject_tool_usage_contract(messages, call_kwargs)

        self.assertEqual(prepared[0], {"role": "system", "content": TOOL_USAGE_CONTRACT_PROMPT})
        self.assertIn(NETWORK_DECISION_PROMPT, TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("must actually call web_search", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("When no tool was called", TOOL_USAGE_CONTRACT_PROMPT)

    def test_tool_usage_contract_deduplicates_by_section_identity_after_body_changes(self):
        from app.services.stream.agent_loop_request_prep import inject_tool_usage_contract

        existing = PromptMessage(
            role="system",
            content="管理员热更新后的任意工具规则正文",
            section_id="tool_usage_contract",
        )
        prepared = inject_tool_usage_contract(
            [existing, PromptMessage(role="user", content="最新公告")],
            {"tools": [{"type": "function", "function": {"name": "web_search"}}]},
        )

        self.assertEqual(
            [message.section_id for message in prepared].count("tool_usage_contract"),
            1,
        )
        self.assertIs(prepared[0], existing)

    def test_no_tool_network_boundary_uses_centralized_prompt(self):
        from app.ai.prompts.agent_loop import NO_TOOL_NETWORK_BOUNDARY_PROMPT
        from app.services.stream.agent_loop_request_prep import inject_no_tool_network_boundary

        messages = [{"role": "user", "content": "OpenAI 最近公告"}]
        prepared = inject_no_tool_network_boundary(messages, call_kwargs={})

        self.assertEqual(prepared[0], {"role": "system", "content": NO_TOOL_NETWORK_BOUNDARY_PROMPT})
        self.assertIn("no web search or URL reading tool", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertIn("Do not claim or imply that you searched", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertIn("cannot verify them in real time", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertIn("Never present existing knowledge as current verification", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertIn("Do not describe missing tools as a system failure", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertIn("Answer ordinary stable questions directly", NO_TOOL_NETWORK_BOUNDARY_PROMPT)
        self.assertNotIn("switch models", NO_TOOL_NETWORK_BOUNDARY_PROMPT)

    def test_tool_usage_contract_defines_autonomous_search_decision_matrix(self):
        from app.ai.prompts.agent_loop import TOOL_USAGE_CONTRACT_PROMPT

        self.assertIn("Do not decide whether to use a tool merely", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("question itself requires current external facts", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("How do I use WeChat A2A interoperability?", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("What products has OpenAI released recently?", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("Hello, who are you?", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("What is 1+1?", TOOL_USAGE_CONTRACT_PROMPT)
        self.assertIn("Do not use web_search", TOOL_USAGE_CONTRACT_PROMPT)

    async def test_prepare_messages_injects_extra_system_prompts_without_user_preprocess(self):
        async def build_llm_messages_fn(
            _raw_messages,
            _has_vision,
            _repo,
            _user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            return [
                {"role": "user", "content": "原问题"},
                {"role": "assistant", "content": "旧回答"},
            ]

        async def should_not_preprocess_url(*_args, **_kwargs):
            raise AssertionError("continuation 不应重新跑 URL 预处理")

        def should_not_inject_file_content(*_args, **_kwargs):
            raise AssertionError("continuation 不应重新跑文件预处理")

        prepared = await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            raw_messages=[],
            has_vision=False,
            file_ids=["file-1"],
            original_message="https://example.com",
            call_config=build_agent_loop_call_config(
                provider="openai",
                options={},
                capabilities={"functionCalling": False},
            ),
            file_repo_factory=lambda _db: object(),
            load_user_system_prompt_fn=lambda _db, _user_id: None,
            build_llm_messages_fn=build_llm_messages_fn,
            is_image_file_fn=lambda _file_id, _repo: False,
            inject_file_content_fn=should_not_inject_file_content,
            preprocess_url_in_message_fn=should_not_preprocess_url,
            preprocess_user_input=False,
            extra_system_prompts=["continuation_system"],
        )

        self.assertEqual(prepared.initial_content_blocks, [])
        self.assertIn("Continue the previous response", prepared.messages[1].content)
        self.assertEqual(prepared.messages[4]["role"], "user")
        self.assertEqual(
            [message.section_id for message in prepared.messages[:4]],
            ["app_identity", "continuation_system", "no_tool_network_boundary", "current_date"],
        )
        self.assertTrue(all(message.section_id is None for message in prepared.messages[4:]))

    async def test_prepare_messages_passes_conversation_scope_to_builder(self):
        build_calls = []

        async def build_llm_messages_fn(
            raw_messages,
            has_vision,
            repo,
            user_system_prompt,
            *,
            user_id=None,
            conversation_id=None,
            include_base_system=True,
        ):
            build_calls.append(
                {
                    "raw_messages": raw_messages,
                    "has_vision": has_vision,
                    "repo": repo,
                    "user_system_prompt": user_system_prompt,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                }
            )
            return [{"role": "user", "content": "看图"}]

        file_repo = object()

        await prepare_agent_loop_messages(
            db=object(),
            user_id="user-1",
            conversation_id="conv-1",
            raw_messages=["raw"],
            has_vision=True,
            file_ids=None,
            original_message="看图",
            call_config=build_agent_loop_call_config(
                provider="qwen",
                options={},
                capabilities={"functionCalling": False, "vision": True},
            ),
            file_repo_factory=lambda _db: file_repo,
            load_user_system_prompt_fn=lambda _db, _user_id: "用户偏好",
            build_llm_messages_fn=build_llm_messages_fn,
            preprocess_user_input=False,
        )

        self.assertEqual(
            build_calls,
            [
                {
                    "raw_messages": ["raw"],
                    "has_vision": True,
                    "repo": file_repo,
                    "user_system_prompt": None,
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
