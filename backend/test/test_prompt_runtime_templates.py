import unittest
from unittest.mock import patch


class PromptRuntimeTemplatesTests(unittest.TestCase):
    def test_prompt_manager_uses_active_bundle_template(self):
        """P0：prompt_manager 只认 active bundle，不再读 legacy prompt_template。"""

        from app.ai.prompts.prompt_manager import prompt_manager

        with patch(
            "app.ai.prompts.prompt_manager.resolve_prompt_template_with_metadata",
            return_value=("标题：{content}", {}),
        ) as resolver:
            prompt = prompt_manager.format_prompt("generate_title", content="Redis")

        self.assertEqual(prompt, "标题：Redis")
        resolver.assert_called_once()
        self.assertEqual(resolver.call_args.args[0], "generate_title")

    def test_all_catalog_prompts_resolve_through_runtime_template(self):
        """P0：catalog 每一项都必须走真实解析路径，不得直接 return 代码常量。"""

        import app.ai.prompts.prompt_manager  # noqa: F401  导入即注册消费方
        from app.ai.prompts import agent_loop
        from app.core.prompt_catalog import KNOWN_UNCONSUMED_KEYS, PROMPT_SPEC_BY_KEY, registered_prompt_consumers

        consumers = registered_prompt_consumers()
        self.assertEqual(set(consumers), set(PROMPT_SPEC_BY_KEY) - KNOWN_UNCONSUMED_KEYS)

        with patch.object(agent_loop, "get_runtime_prompt_template", return_value="运行时正文") as resolver:
            for key in (
                "app_identity",
                "tool_usage_contract",
                "no_tool_network_boundary",
                "no_vision_file_boundary",
                "url_read_tool_description",
                "limit_summary",
                "continuation_system",
            ):
                with self.subTest(key=key):
                    self.assertEqual(consumers[key](), "运行时正文")
        self.assertEqual(resolver.call_count, 7)

    def test_plan_control_prompt_stays_code_owned(self):
        """计划控制不在 catalog 内，本期继续由代码维护。"""

        from app.ai.prompts import agent_loop

        with patch.object(agent_loop, "get_runtime_prompt_template", side_effect=AssertionError("不应读取运行时模板")):
            self.assertEqual(agent_loop.get_agent_plan_control_prompt("on"), agent_loop.AGENT_PLAN_CONTROL_ON_PROMPT)
            self.assertEqual(
                agent_loop.get_agent_plan_control_prompt("auto"), agent_loop.AGENT_PLAN_CONTROL_AUTO_PROMPT
            )

    def test_summary_and_url_description_keep_runtime_resolution(self):
        from app.ai.prompts import agent_loop

        with patch.object(agent_loop, "get_runtime_prompt_template", return_value="保留运行时模板"):
            self.assertEqual(agent_loop.get_limit_summary_prompt(), "保留运行时模板")
            self.assertEqual(agent_loop.get_url_read_tool_description(), "保留运行时模板")

    def test_build_url_read_tool_uses_runtime_description(self):
        from app.ai import tools

        with patch(
            "app.ai.tools.get_url_read_tool_description",
            return_value="动态读取网页说明",
            create=True,
        ):
            tool = tools.build_url_read_tool()

        self.assertEqual(tool["function"]["description"], "动态读取网页说明")

    def test_message_builder_uses_shared_base_template(self):
        from app.ai.prompts import system_prompt
        from app.services.chat import message_builder

        with patch.object(
            system_prompt,
            "get_app_identity_prompt",
            return_value="运行时 Fusion 身份规则",
            create=True,
        ):
            messages = self._run_async(message_builder.build_llm_messages([], has_vision=False, file_repo=None))

        self.assertEqual(messages[0], {"role": "system", "content": "运行时 Fusion 身份规则"})

    def test_agent_loop_request_prep_injects_runtime_tool_contract_prompt(self):
        from app.services.stream import agent_loop_request_prep

        call_kwargs = {"tools": [{"type": "function", "function": {"name": "web_search"}}]}
        with patch.object(
            agent_loop_request_prep,
            "get_tool_usage_contract_prompt",
            return_value="运行时工具一致性规则",
            create=True,
        ):
            messages = agent_loop_request_prep.inject_tool_usage_contract(
                [{"role": "user", "content": "OpenAI 最新公告"}],
                call_kwargs,
            )

        self.assertEqual(messages[0], {"role": "system", "content": "运行时工具一致性规则"})

    def test_limit_summary_uses_runtime_prompt(self):
        from app.services.stream import limit_summary

        messages = []
        with patch.object(
            limit_summary,
            "get_limit_summary_prompt",
            return_value="运行时触顶总结规则",
            create=True,
        ):
            limit_summary.append_limit_summary_prompt(messages)

        content = messages[-1]["content"]
        self.assertIn("运行时触顶总结规则", content)
        self.assertIn("不要向用户提及", content)

    def test_continuation_injects_runtime_prompt(self):
        from app.services.agent import continuation

        with patch.object(
            continuation,
            "get_continuation_system_prompt",
            return_value="运行时继续回答规则",
            create=True,
        ):
            messages = continuation.inject_continuation_prompt([{"role": "user", "content": "继续"}])

        self.assertEqual(messages[0], {"role": "system", "content": "运行时继续回答规则"})

    def test_url_preprocess_uses_runtime_url_read_tool_builder(self):
        from app.services.stream import persistence

        dynamic_tool = {"type": "function", "function": {"name": "url_read", "description": "运行时读取工具"}}
        call_kwargs = {"tools": []}
        with patch.object(
            persistence,
            "build_url_read_tool",
            return_value=dynamic_tool,
            create=True,
        ):
            persistence.ensure_url_read_tool(call_kwargs)
            persistence.ensure_url_read_tool(call_kwargs)

        self.assertEqual(call_kwargs["tools"], [dynamic_tool])

    @staticmethod
    def _run_async(coro):
        import asyncio

        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
