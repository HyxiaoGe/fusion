"""辅助功能（标题、推荐问题）共用轻量模型配置的契约。"""

import unittest
from unittest.mock import patch

from app.services import utility_model


class ResolveUtilityModelTests(unittest.TestCase):
    def test_优先解析轻量辅助模型(self):
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            return_value=("litellm/deepseek-chat", "deepseek", {}),
        ) as resolve:
            model, provider, kwargs = utility_model.resolve_utility_model("qwen-max-latest")

        # 模型与 provider 原样透传；kwargs 额外带上禁用推理（见 DisableThinkingTests）
        self.assertEqual((model, provider), ("litellm/deepseek-chat", "deepseek"))
        resolve.assert_called_once_with(utility_model.UTILITY_MODEL_ID)

    def test_轻量模型不可用时回退到会话模型(self):
        def fake_resolve(model_id):
            if model_id == utility_model.UTILITY_MODEL_ID:
                raise ValueError("未注册")
            return ("litellm/qwen", "dashscope", {})

        with patch.object(utility_model.llm_manager, "resolve_model", side_effect=fake_resolve):
            model, provider, _ = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual((model, provider), ("litellm/qwen", "dashscope"))

    def test_会话模型同样不可用时向上抛出(self):
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            side_effect=ValueError("未注册"),
        ):
            with self.assertRaises(ValueError):
                utility_model.resolve_utility_model("unknown-model")


class DisableThinkingTests(unittest.TestCase):
    """辅助调用必须关掉推理，否则 reasoning token 会吃光 max_tokens。

    线上现象：deepseek-chat 先产 reasoning，512 预算被吃满后 content 为空，
    finish_reason=length 且 raw_chars=0，推荐问题于是报「模型没有返回有效推荐问题」。
    这条路已经撞过一次（128 → 512），继续抬预算只会撞 UTILITY_LLM_TIMEOUT。
    """

    def test_解析结果带上禁用推理(self):
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            return_value=("litellm/deepseek-chat", "deepseek", {}),
        ):
            _, _, kwargs = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(kwargs["extra_body"]["thinking"], {"type": "disabled"})

    def test_回退到会话模型时同样禁用推理(self):
        """回退路径更需要：会话模型很可能正是慢而贵的 thinking 模型。"""

        def fake_resolve(model_id):
            if model_id == utility_model.UTILITY_MODEL_ID:
                raise ValueError("未注册")
            return ("litellm/qwen", "dashscope", {})

        with patch.object(utility_model.llm_manager, "resolve_model", side_effect=fake_resolve):
            _, _, kwargs = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(kwargs["extra_body"]["thinking"], {"type": "disabled"})

    def test_不覆盖模型自带的其他_extra_body_字段(self):
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            return_value=("litellm/x", "p", {"extra_body": {"foo": "bar"}, "temperature": 0}),
        ):
            _, _, kwargs = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(kwargs["extra_body"]["foo"], "bar")
        self.assertEqual(kwargs["extra_body"]["thinking"], {"type": "disabled"})
        self.assertEqual(kwargs["temperature"], 0)

    def test_不改动调用方传入的原始配置(self):
        """llm_manager 返回的可能是共享配置对象，就地改会污染其他调用方。"""
        original = {"extra_body": {"foo": "bar"}}
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            return_value=("litellm/x", "p", original),
        ):
            utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(original, {"extra_body": {"foo": "bar"}})


class UtilityModelSingleSourceTests(unittest.TestCase):
    """两个消费方必须共用同一份配置，避免超时与模型选择再次分叉。"""

    def test_超时必须小于中间件超时(self):
        """辅助调用必须先于 TimeoutMiddleware 掐断自己抛错，否则会把 408 吐给前端。"""
        import inspect

        import main

        middleware_timeout = inspect.signature(
            main.TimeoutMiddleware.__init__
        ).parameters["timeout_seconds"].default

        self.assertLess(utility_model.UTILITY_LLM_TIMEOUT, middleware_timeout)

    def test_消费方不再各自定义辅助模型常量(self):
        from app.services import chat_service, suggested_question_service

        for module, owner in (
            (chat_service, chat_service.ChatService),
            (suggested_question_service, suggested_question_service.SuggestedQuestionService),
        ):
            with self.subTest(owner=owner.__name__):
                self.assertFalse(
                    hasattr(owner, "UTILITY_MODEL_ID"),
                    "辅助模型 ID 必须只在 utility_model 中定义",
                )
                self.assertFalse(
                    hasattr(owner, "UTILITY_LLM_TIMEOUT"),
                    "辅助模型超时必须只在 utility_model 中定义",
                )


if __name__ == "__main__":
    unittest.main()
