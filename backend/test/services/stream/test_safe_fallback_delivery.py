"""安全兜底的实际正文交付、语言与终态回归。"""

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from app.schemas.chat import Usage
from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest, handle_agent_round_outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.limit_summary import run_limit_summary_step
from app.services.stream.safe_fallback_response import FallbackResponseContext
from test.services.stream import test_limit_summary as summary_fixtures
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class SafeFallbackDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_正常答案不触发兜底语言选择(self):
        request = AgentRoundOutcomeRequest(
            db=None,
            messages=[],
            state=AgentLoopState(),
            runtime=_runtime(fallback_response_context=FallbackResponseContext("Hello")),
            step_number=1,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="Hello!",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(),
                output_deferred=True,
            ),
        )
        with (
            patch("app.services.stream.agent_loop_round_outcome.append_chunk", new=AsyncMock()) as append,
            patch("app.services.stream.agent_loop_round_outcome.complete_text_response_step", new=AsyncMock()),
            patch("app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock()) as llm,
        ):
            await handle_agent_round_outcome(request=request)
        self.assertEqual(append.await_args.args[2], "Hello!")
        llm.assert_not_awaited()

    async def test_协议修复耗尽后的本地化正文必须原样保存(self):
        request, prepare_context_fn = summary_fixtures.LimitSummaryNoEvidenceFactBoundaryTests()._standard_request(
            answer="",
            content_blocks=[],
            user_message="请用英语解释这个概念。",
        )
        request = replace(
            request,
            fallback_response_context=FallbackResponseContext("请用英语解释这个概念。", "en"),
            stream_round_fn=AsyncMock(
                return_value=(
                    "",
                    "",
                    [{"id": "tc-invalid", "name": "web_search", "arguments": "{}"}],
                    "tool_calls",
                    Usage(),
                )
            ),
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append,
        ):
            result = await run_limit_summary_step(request=request)
        text = append.await_args.args[2]
        self.assertTrue(text.isascii(), text)
        self.assertTrue(result.incomplete)
        self.assertEqual(request.content_blocks[-1].text, text)
