"""推荐问题生成必须留下可聚合的耗时记录。

封口前送达的预算（2 秒）此前只能靠 ready 事件的 duration_ms 评估，而该事件
只在赶上窗口时才发出——恰恰在需要数据的「没赶上」那一侧什么都不留。这里让
模型调用本身无论成败都记录耗时与输出 token，便于判断是模型吐得多还是上游慢。
"""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.suggested_question_service import SuggestedQuestionService

_MARKER = "suggested_questions_generate"


def _response(content: str, *, completion_tokens=None):
    usage = SimpleNamespace(completion_tokens=completion_tokens) if completion_tokens is not None else None
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


class SuggestionTimingLogTests(unittest.TestCase):
    def _run(self, acompletion):
        service = SuggestedQuestionService.__new__(SuggestedQuestionService)
        with (
            patch("app.services.suggested_question_service.litellm.acompletion", new=acompletion),
            patch(
                "app.services.suggested_question_service.resolve_utility_model",
                return_value=("litellm_proxy/deepseek-chat", "deepseek", {}),
            ),
            patch(
                "app.services.suggested_question_service.prompt_manager.format_prompt_with_metadata",
                return_value=("提示词", {}),
            ),
            self.assertLogs("app", level=logging.INFO) as captured,
        ):
            asyncio.run(service._generate("对话内容", "deepseek-chat", revision=1))
        return [line for line in captured.output if _MARKER in line]

    def test_成功时记录耗时与输出_token(self):
        async def ok(**_kwargs):
            return _response("1. 一\n2. 二\n3. 三", completion_tokens=64)

        lines = self._run(ok)
        self.assertEqual(len(lines), 1)
        self.assertIn("result=ready", lines[0])
        self.assertIn("duration_ms=", lines[0])
        self.assertIn("completion_tokens=64", lines[0])

    def test_失败时同样记录耗时(self):
        """没赶上送达窗口那一侧正是最需要数据的地方，不能只在成功时记。"""

        async def boom(**_kwargs):
            raise RuntimeError("upstream down")

        lines = self._run(boom)
        self.assertEqual(len(lines), 1)
        self.assertIn("result=failed", lines[0])
        self.assertIn("duration_ms=", lines[0])

    def test_缺少_usage_时不报错(self):
        async def no_usage(**_kwargs):
            return _response("1. 一\n2. 二\n3. 三")

        lines = self._run(no_usage)
        self.assertEqual(len(lines), 1)
        self.assertIn("completion_tokens=None", lines[0])

    def test_日志不含对话与问题正文(self):
        async def ok(**_kwargs):
            return _response("1. 机密问题一\n2. 二\n3. 三", completion_tokens=10)

        lines = self._run(ok)
        self.assertNotIn("机密问题一", lines[0])
        self.assertNotIn("对话内容", lines[0])


if __name__ == "__main__":
    unittest.main()
