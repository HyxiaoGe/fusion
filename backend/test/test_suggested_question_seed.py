"""「换一批」必须真的换一批。

同一条消息的多次生成，prompt 与模型参数此前完全相同，代理按请求负载命中缓存，
于是 revision 推进了、问题文本却一模一样。用 revision 作为 seed 让每一批的请求
负载不同，从而拿到新的采样。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.suggested_question_service import (
    SuggestedQuestionClaim,
    SuggestedQuestionService,
)


def _claim(revision: int) -> SuggestedQuestionClaim:
    return SuggestedQuestionClaim(
        message_id="msg-1",
        conversation_id="conv-1",
        revision=revision,
        model_id="deepseek-chat",
    )


class SuggestedQuestionSeedTests(unittest.TestCase):
    def _capture(self, revision: int) -> dict:
        service = SuggestedQuestionService.__new__(SuggestedQuestionService)
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="1. 一\n2. 二\n3. 三"))]
            )

        with (
            patch("app.services.suggested_question_service.litellm.acompletion", new=fake_acompletion),
            patch(
                "app.services.suggested_question_service.resolve_utility_model",
                return_value=("litellm_proxy/deepseek-chat", "deepseek", {}),
            ),
            patch(
                "app.services.suggested_question_service.prompt_manager.format_prompt_with_metadata",
                return_value=("提示词", {}),
            ),
        ):
            asyncio.run(service._generate("对话内容", "deepseek-chat", revision=revision))
        return captured

    def test_seed_取自_revision(self):
        self.assertEqual(self._capture(1)["seed"], 1)

    def test_换一批推进_revision_后_seed_随之改变(self):
        """请求负载不同，代理才不会把上一批原样返回。"""
        first = self._capture(1)
        second = self._capture(2)

        self.assertNotEqual(first["seed"], second["seed"])
        # 除 seed 外其余请求参数保持一致，避免顺带改变生成风格。
        self.assertEqual(first["messages"], second["messages"])
        self.assertEqual(first["max_tokens"], second["max_tokens"])
        self.assertEqual(first["timeout"], second["timeout"])

    def test_缺少_revision_时不带_seed(self):
        """旧调用方不传 revision 时保持原有请求形状。"""
        service = SuggestedQuestionService.__new__(SuggestedQuestionService)
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="1. 一\n2. 二\n3. 三"))]
            )

        with (
            patch("app.services.suggested_question_service.litellm.acompletion", new=fake_acompletion),
            patch(
                "app.services.suggested_question_service.resolve_utility_model",
                return_value=("litellm_proxy/deepseek-chat", "deepseek", {}),
            ),
            patch(
                "app.services.suggested_question_service.prompt_manager.format_prompt_with_metadata",
                return_value=("提示词", {}),
            ),
        ):
            asyncio.run(service._generate("对话内容", "deepseek-chat"))

        self.assertNotIn("seed", captured)


class GenerateClaimedQuestionsSeedTests(unittest.TestCase):
    def test_claim_的_revision_传到模型调用(self):
        service = SuggestedQuestionService.__new__(SuggestedQuestionService)
        generate = AsyncMock(return_value=["一", "二", "三"])

        with (
            patch.object(SuggestedQuestionService, "build_dialog_content", return_value="对话"),
            patch.object(SuggestedQuestionService, "_generate", new=generate),
            patch.object(SuggestedQuestionService, "store_generated_questions", return_value=True),
        ):
            asyncio.run(service.generate_claimed_questions(_claim(3)))

        self.assertEqual(generate.await_args.kwargs["revision"], 3)


if __name__ == "__main__":
    unittest.main()
