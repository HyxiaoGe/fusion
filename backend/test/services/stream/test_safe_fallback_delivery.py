"""安全兜底的实际正文交付与终态回归。"""

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from app.schemas.chat import Usage
from app.services.stream.limit_summary import run_limit_summary_step
from app.services.stream.safe_fallback_response import PROTOCOL_FALLBACK_TEXT
from test.services.stream import test_limit_summary as summary_fixtures


class SafeFallbackDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_协议修复耗尽后直接交付中文兜底且不再调用模型判断语言(self):
        request, prepare_context_fn = summary_fixtures.LimitSummaryNoEvidenceFactBoundaryTests()._standard_request(
            answer="",
            content_blocks=[],
            user_message="请用英语解释这个概念。",
        )
        request = replace(
            request,
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
            patch("app.services.stream_state_service.append_chunk", new=AsyncMock()) as append,
            patch("litellm.acompletion", new=AsyncMock()) as direct_llm,
        ):
            result = await run_limit_summary_step(request=request)
        text = append.await_args.args[2]
        self.assertEqual(text, PROTOCOL_FALLBACK_TEXT)
        self.assertTrue(result.incomplete)
        self.assertEqual(request.content_blocks[-1].text, text)
        direct_llm.assert_not_awaited()
