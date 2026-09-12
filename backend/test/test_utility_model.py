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
            result = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(result, ("litellm/deepseek-chat", "deepseek", {}))
        resolve.assert_called_once_with(utility_model.UTILITY_MODEL_ID)

    def test_轻量模型不可用时回退到会话模型(self):
        def fake_resolve(model_id):
            if model_id == utility_model.UTILITY_MODEL_ID:
                raise ValueError("未注册")
            return ("litellm/qwen", "dashscope", {})

        with patch.object(utility_model.llm_manager, "resolve_model", side_effect=fake_resolve):
            result = utility_model.resolve_utility_model("qwen-max-latest")

        self.assertEqual(result, ("litellm/qwen", "dashscope", {}))

    def test_会话模型同样不可用时向上抛出(self):
        with patch.object(
            utility_model.llm_manager,
            "resolve_model",
            side_effect=ValueError("未注册"),
        ):
            with self.assertRaises(ValueError):
                utility_model.resolve_utility_model("unknown-model")


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
