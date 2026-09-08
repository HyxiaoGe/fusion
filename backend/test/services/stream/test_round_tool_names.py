"""当轮工具目录记录的契约。"""

import unittest
from unittest.mock import AsyncMock

from app.services.stream.llm_round_lifecycle import LLMRoundLifecycle, round_tool_names


def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {}}}


class RoundToolNamesTests(unittest.TestCase):
    def test_按请求顺序提取工具名(self):
        call_kwargs = {"tools": [_tool("web_search"), _tool("url_read")]}
        self.assertEqual(round_tool_names(call_kwargs), ["web_search", "url_read"])

    def test_裁剪后的目录才是当轮真实可见范围(self):
        """驱动层按研究阶段裁剪后，事件必须反映裁剪结果而不是开跑时的宣告目录。"""
        announced = {"tools": [_tool("web_search"), _tool("url_read"), _tool("map_query")]}
        allowed = frozenset({"web_search"})
        filtered = {
            "tools": [
                tool for tool in announced["tools"]
                if tool["function"]["name"] in allowed
            ]
        }
        self.assertEqual(round_tool_names(filtered), ["web_search"])
        self.assertNotEqual(round_tool_names(filtered), round_tool_names(announced))

    def test_无工具轮返回空表而非报错(self):
        self.assertEqual(round_tool_names({}), [])
        self.assertEqual(round_tool_names({"tools": None}), [])
        self.assertEqual(round_tool_names(None), [])

    def test_忽略结构异常的条目(self):
        call_kwargs = {"tools": [_tool("web_search"), {"type": "function"}, "junk", {"function": {}}]}
        self.assertEqual(round_tool_names(call_kwargs), ["web_search"])


class LlmRoundStartedEmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_当轮目录随事件发出(self):
        emitter = AsyncMock()
        await LLMRoundLifecycle.start(
            emitter=emitter,
            observation=AsyncMock(),
            round_index=2,
            model="deepseek-chat",
            provider="deepseek",
            parent_step_id="step-1",
            tool_names=["web_search"],
        )
        kwargs = emitter.llm_round_started.await_args.kwargs
        self.assertEqual(kwargs["tool_names"], ["web_search"])

    async def test_未传目录时不写入该字段(self):
        """滚动发布期旧调用方不传，事件保持默认空表而不是报错。"""
        emitter = AsyncMock()
        await LLMRoundLifecycle.start(
            emitter=emitter,
            observation=AsyncMock(),
            round_index=1,
            model="deepseek-chat",
            provider="deepseek",
            parent_step_id="step-1",
        )
        self.assertNotIn("tool_names", emitter.llm_round_started.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
