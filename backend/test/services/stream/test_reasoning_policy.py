import unittest

from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs


class ReasoningPolicyTests(unittest.TestCase):
    def test_deepseek_thinking_tools_remove_tool_choice_and_preserve_extra_body(self):
        original = {
            "tools": [{"function": {"name": "web_search"}}],
            "tool_choice": "auto",
            "extra_body": {"trace": "kept"},
        }

        configured = configure_reasoning_call_kwargs(
            original,
            provider="deepseek",
            should_use_reasoning=True,
        )

        self.assertNotIn("tool_choice", configured)
        self.assertEqual(
            configured["extra_body"],
            {"trace": "kept", "thinking": {"type": "enabled"}},
        )
        self.assertEqual(original["tool_choice"], "auto")

    def test_gemini_requests_reasoning_content_mapping(self):
        configured = configure_reasoning_call_kwargs(
            {"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"},
            provider="gemini",
            should_use_reasoning=True,
        )

        self.assertEqual(configured["reasoning_effort"], "high")
        self.assertEqual(configured["tool_choice"], "auto")

    def test_volcengine_disables_thinking_only_while_tools_are_present(self):
        tool_round = configure_reasoning_call_kwargs(
            {
                "tools": [{"function": {"name": "web_search"}}],
                "extra_body": {"trace": "kept"},
            },
            provider="volcengine",
            should_use_reasoning=True,
        )
        synthesis_round = configure_reasoning_call_kwargs(
            {
                "extra_body": {"trace": "kept", "thinking": {"type": "disabled"}},
            },
            provider="volcengine",
            should_use_reasoning=True,
        )

        self.assertEqual(
            tool_round["extra_body"],
            {"trace": "kept", "thinking": {"type": "disabled"}},
        )
        self.assertEqual(synthesis_round["extra_body"], {"trace": "kept"})

    def test_forbidden_tool_round_is_configured_as_tool_free(self):
        forbidden = {"tools": [{"function": {"name": "web_search"}}], "tool_choice": "none"}

        deepseek = configure_reasoning_call_kwargs(forbidden, provider="deepseek", should_use_reasoning=True)
        volcengine = configure_reasoning_call_kwargs(
            {**forbidden, "extra_body": {"trace": "kept"}},
            provider="volcengine",
            should_use_reasoning=True,
        )

        # 禁止调用只公告定义：DeepSeek 保留 tool_choice=none，豆包不因工具定义关闭思考。
        self.assertEqual(deepseek["tool_choice"], "none")
        self.assertEqual(volcengine["extra_body"], {"trace": "kept"})

    def test_reasoning_override_off_leaves_provider_parameters_untouched(self):
        original = {"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"}

        configured = configure_reasoning_call_kwargs(
            original,
            provider="deepseek",
            should_use_reasoning=False,
        )

        self.assertEqual(configured, original)

    def test_reasoning_off_disables_thinking_for_switchable_models(self):
        original = {
            "tools": [{"function": {"name": "web_search"}}],
            "tool_choice": "auto",
            "extra_body": {"trace": "kept"},
        }

        configured = configure_reasoning_call_kwargs(
            original,
            provider="openai",
            should_use_reasoning=False,
            thinking_switchable=True,
        )

        self.assertEqual(
            configured["extra_body"],
            {"trace": "kept", "extra_body": {"thinking": {"type": "disabled"}}},
        )
        self.assertNotIn("thinking", configured["extra_body"])
        self.assertEqual(configured["tool_choice"], "auto")
        self.assertEqual(original["extra_body"], {"trace": "kept"})

    def test_reasoning_off_disables_gemini_thinking_via_reasoning_effort(self):
        configured = configure_reasoning_call_kwargs(
            {"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"},
            provider="gemini",
            should_use_reasoning=False,
            thinking_switchable=True,
        )

        self.assertEqual(configured["reasoning_effort"], "none")
        self.assertNotIn("extra_body", configured)
        self.assertEqual(configured["tool_choice"], "auto")

    def test_reasoning_off_reconfigure_keeps_disabled_thinking(self):
        first_round = configure_reasoning_call_kwargs(
            {"tools": [{"function": {"name": "web_search"}}]},
            provider="openai",
            should_use_reasoning=False,
            thinking_switchable=True,
        )

        later_round = configure_reasoning_call_kwargs(
            first_round,
            provider="openai",
            should_use_reasoning=False,
        )

        self.assertEqual(later_round, first_round)

    def test_reasoning_on_ignores_switchable_flag(self):
        configured = configure_reasoning_call_kwargs(
            {"tools": [{"function": {"name": "web_search"}}]},
            provider="openai",
            should_use_reasoning=True,
            thinking_switchable=True,
        )

        self.assertNotIn("extra_body", configured)


if __name__ == "__main__":
    unittest.main()
