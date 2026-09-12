"""attempt 事件必须透传工具已给出的错误码。

此前失败路径一律写 None，导致排查工具失败时必须回头 join tool_call_logs
才能拿到原因；MCP 侧本就有完整的错误码词汇表。
"""

import unittest
from unittest.mock import AsyncMock

from app.services.stream.tool_executor import ToolAttemptLifecycle
from app.services.tool_handlers.base import ToolResult


def _runner():
    emitter = AsyncMock()
    return emitter, ToolAttemptLifecycle(emitter=emitter, tool_call_id="call-1", tool_name="weather_forecast")


class AttemptErrorCodeTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, result):
        emitter, runner = _runner()
        await runner.execute(AsyncMock(return_value=result))
        return emitter.tool_attempt_completed.await_args.kwargs

    async def test_失败时透传工具给出的错误码(self):
        kwargs = await self._run(ToolResult(status="failed", data={"error_code": "tool_error"}))
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error_code"], "tool_error")

    async def test_透传各类上游错误码(self):
        for code in ("call_timeout", "invalid_response", "server_circuit_open", "server_run_budget_exhausted"):
            with self.subTest(code=code):
                kwargs = await self._run(ToolResult(status="failed", data={"error_code": code}))
                self.assertEqual(kwargs["error_code"], code)

    async def test_工具没给码时保持为空(self):
        kwargs = await self._run(ToolResult(status="failed", data={}))
        self.assertEqual(kwargs["status"], "failed")
        self.assertIsNone(kwargs["error_code"])

    async def test_非字符串错误码视为缺失(self):
        for value in (123, {"code": "x"}, [], ""):
            with self.subTest(value=value):
                kwargs = await self._run(ToolResult(status="failed", data={"error_code": value}))
                self.assertIsNone(kwargs["error_code"])

    async def test_超时仍归为独立状态(self):
        kwargs = await self._run(ToolResult(status="failed", data={"error_code": "tool_timeout"}))
        self.assertEqual(kwargs["status"], "timeout")
        self.assertEqual(kwargs["error_code"], "tool_timeout")

    async def test_成功与降级不带错误码(self):
        """degraded 目前仍记为 success：改动它会翻转既有 span 语义，另案处理。"""
        for status in ("success", "degraded"):
            with self.subTest(status=status):
                kwargs = await self._run(ToolResult(status=status, data={"error_code": "stale"}))
                self.assertEqual(kwargs["status"], "success")
                self.assertIsNone(kwargs["error_code"])

    async def test_异常逃逸时没有结果可取码(self):
        emitter, runner = _runner()
        with self.assertRaises(RuntimeError):
            await runner.execute(AsyncMock(side_effect=RuntimeError("boom")))
        kwargs = emitter.tool_attempt_completed.await_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertIsNone(kwargs["error_code"])


if __name__ == "__main__":
    unittest.main()
