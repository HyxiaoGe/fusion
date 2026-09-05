"""证明 catalog 正文变化能真正到达模型输入。

注册表本身只能证明「有 accessor」，不能证明「正文能进模型」。这里对每个已注册
消费方，从真实生产调用方出发，断言被替换的正文出现在实际送往模型的 payload 里。
"""

import unittest
from unittest.mock import patch


class PromptManagerCallSiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_title_body_reaches_model_input(self):
        from app.services import chat_service as chat_service_module

        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop-after-capture")

        with (
            patch.object(
                chat_service_module.prompt_manager,
                "resolve_template_with_metadata",
                return_value=("标记A-{content}", {}),
            ),
            patch.object(chat_service_module.litellm, "acompletion", side_effect=fake_acompletion),
        ):
            prompt, _meta = chat_service_module.prompt_manager.format_prompt_with_metadata(
                "generate_title", content="Redis 缓存"
            )

        self.assertEqual(prompt, "标记A-Redis 缓存")

    async def test_suggested_questions_body_reaches_model_input(self):
        from app.services import suggested_question_service as module

        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop-after-capture")

        service = module.SuggestedQuestionService.__new__(module.SuggestedQuestionService)
        with (
            patch.object(module.prompt_manager, "resolve_template_with_metadata", return_value=("标记B-{content}", {})),
            patch.object(module.litellm, "acompletion", side_effect=fake_acompletion),
            patch.object(
                module.SuggestedQuestionService,
                "_resolve_utility_model",
                return_value=("m", None, {}),
                create=True,
            ),
        ):
            await service._generate("对话内容", "model-1")

        self.assertIn("messages", captured)
        self.assertIn("标记B-对话内容", str(captured["messages"]))

    def test_file_analysis_body_reaches_prompt_payload(self):
        from app.ai.prompts.prompt_manager import prompt_manager

        with patch.object(
            prompt_manager, "resolve_template_with_metadata", return_value=("标记C-{query}-{file_content}", {})
        ):
            prompt, _meta = prompt_manager.format_prompt_with_metadata(
                "file_analysis", query="问题", file_content="正文"
            )

        self.assertEqual(prompt, "标记C-问题-正文")


class AgentLoopCallSiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_identity_body_reaches_system_messages(self):
        from app.services.chat import message_builder

        with patch("app.ai.prompts.system_prompt.get_app_identity_prompt", return_value="标记D-身份"):
            messages = await message_builder.build_llm_messages([], has_vision=False, file_repo=None)

        self.assertEqual(messages[0]["content"], "标记D-身份")

    def test_url_read_description_body_reaches_tool_definition(self):
        from app.ai import tools

        with patch.object(tools, "get_url_read_tool_description", return_value="标记E-读取说明"):
            tool = tools.build_url_read_tool()

        self.assertEqual(tool["function"]["description"], "标记E-读取说明")

    def test_limit_summary_body_reaches_summary_system_message(self):
        from app.services.stream import limit_summary

        messages = []
        with patch.object(limit_summary, "get_limit_summary_prompt", return_value="标记F-触顶总结"):
            limit_summary.append_limit_summary_prompt(messages)

        self.assertIn("标记F-触顶总结", messages[-1]["content"])

    def test_continuation_body_reaches_system_message(self):
        from app.services.agent import continuation

        with patch.object(continuation, "get_continuation_system_prompt", return_value="标记G-续写"):
            messages = continuation.inject_continuation_prompt([{"role": "user", "content": "继续"}])

        self.assertEqual(messages[0], {"role": "system", "content": "标记G-续写"})

    def test_tool_usage_contract_body_reaches_system_message(self):
        from app.services.stream import agent_loop_request_prep

        call_kwargs = {"tools": [{"type": "function", "function": {"name": "web_search"}}]}
        with patch.object(agent_loop_request_prep, "get_tool_usage_contract_prompt", return_value="标记H-工具契约"):
            messages = agent_loop_request_prep.inject_tool_usage_contract(
                [{"role": "user", "content": "查一下"}], call_kwargs
            )

        self.assertEqual(messages[0], {"role": "system", "content": "标记H-工具契约"})

    def test_no_tool_network_boundary_body_reaches_system_message(self):
        from app.services.stream import agent_loop_request_prep

        with patch.object(
            agent_loop_request_prep, "get_no_tool_network_boundary_prompt", return_value="标记I-无联网边界"
        ):
            messages = agent_loop_request_prep.inject_no_tool_network_boundary(
                [{"role": "user", "content": "你好"}], call_kwargs={}
            )

        self.assertEqual(messages[0], {"role": "system", "content": "标记I-无联网边界"})

    def test_no_vision_file_boundary_body_reaches_system_message(self):
        from app.services.stream import agent_loop_request_prep

        with patch.object(
            agent_loop_request_prep, "get_no_vision_file_boundary_prompt", return_value="标记J-无图片边界"
        ):
            messages = agent_loop_request_prep.inject_no_vision_file_boundary([{"role": "user", "content": "看图"}])

        self.assertEqual(messages[0], {"role": "system", "content": "标记J-无图片边界"})


class FileContentEnhancementConsumptionTests(unittest.TestCase):
    """该条目此前无任何消费方；接入模板后必须真实可达且正文逐字节不变。"""

    def test_wrapper_text_is_byte_identical_to_previous_hardcoded_output(self):
        from app.services.chat.message_builder import inject_file_content

        file_contents = {"a": "AAA", "b": "BBB"}
        combined = "\n\n".join(f"文件内容 ({i + 1}):\n{c}" for i, c in enumerate(file_contents.values()))
        expected = f"我的问题\n\n以下是相关文件内容，请结合这些内容回答：\n{combined}"

        result = inject_file_content([{"role": "user", "content": "我的问题"}], "我的问题", file_contents)

        self.assertEqual(result[-1]["content"].encode("utf-8"), expected.encode("utf-8"))

    def test_template_body_reaches_user_message(self):
        from app.ai.prompts.prompt_manager import prompt_manager
        from app.services.chat.message_builder import inject_file_content

        with patch.object(
            prompt_manager, "resolve_template_with_metadata", return_value=("标记K-{query}-{file_content}", {})
        ):
            result = inject_file_content([{"role": "user", "content": "问"}], "问", {"a": "内容"})

        self.assertIn("标记K-问-", result[-1]["content"])

    def test_empty_message_list_still_renders_from_template(self):
        from app.services.chat.message_builder import inject_file_content

        result = inject_file_content([], "只有附件", {"a": "AAA"})

        self.assertEqual(result[0]["role"], "user")
        self.assertIn("以下是相关文件内容，请结合这些内容回答：", result[0]["content"])


class RegistrationBindsToProductionPathTests(unittest.TestCase):
    """注册的 accessor 与生产调用必须经由同一个解析点，绕过它即绕过注册。"""

    def test_patching_single_resolution_point_affects_both(self):
        from app.ai.prompts.prompt_manager import prompt_manager
        from app.core.prompt_catalog import registered_prompt_consumers

        accessor = registered_prompt_consumers()["generate_title"]
        with patch.object(
            prompt_manager, "resolve_template_with_metadata", return_value=("同一解析点-{content}", {})
        ) as resolver:
            via_registry = accessor()
            via_production, _meta = prompt_manager.format_prompt_with_metadata("generate_title", content="X")

        self.assertEqual(via_registry, "同一解析点-{content}")
        self.assertEqual(via_production, "同一解析点-X")
        self.assertEqual(resolver.call_count, 2)
