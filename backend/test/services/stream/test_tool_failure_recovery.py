"""失败后的恢复必须跨过模型过早 stop，而不是返回假完成。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.chat import Usage
from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest, handle_agent_round_outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class ToolFailureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def request(self, state, tools=("weather_forecast", "web_search", "url_read")):
        return AgentRoundOutcomeRequest(
            db=None,
            messages=[{"role": "user", "content": "香港未来三天会下雨吗"}],
            state=state,
            runtime=_runtime(emitter=AsyncMock(), complete_step_fn=AsyncMock()),
            step_number=2,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="天气查询失败，请稍后再试。",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                context=None,
                output_deferred=True,
                announced_tool_names=frozenset(tools),
            ),
        )

    async def test_failed_tool_stop_gets_one_recovery_round_with_actual_alternatives(self):
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        request = self.request(state)
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()) as append:
            self.assertIsNone(await handle_agent_round_outcome(request=request))
        append.assert_not_awaited()
        self.assertTrue(state.tool_recovery_prompted)
        self.assertIn("web_search", request.messages[-1]["content"])
        self.assertIn("url_read", request.messages[-1]["content"])
        self.assertEqual(request.messages[-1].section_id, "tool_failure_recovery")

    async def test_exhausted_alternatives_do_not_repair_forever_or_claim_completed(self):
        state = AgentLoopState(product_tool_attempted=True)
        for name in ("weather_forecast", "web_search", "url_read"):
            state.record_tool_outcome(name, "failed")
        request = self.request(state)
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()) as append:
            await handle_agent_round_outcome(request=request)
        self.assertTrue(state.unknown_terminated)
        self.assertNotIn("航班", str(append.await_args_list))
        self.assertFalse(state.tool_recovery_prompted)

    async def test_user_input_repair_does_not_trigger_search_recovery(self):
        state = AgentLoopState(
            product_tool_attempted=True,
            pending_tool_repairs={"r": {"requires_user_input": True, "required_fields": ["location"]}},
        )
        state.record_tool_outcome("weather_forecast", "failed")
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=self.request(state))
        self.assertFalse(state.tool_recovery_prompted)

    async def test_failed_mcp_round_defers_output_before_deciding_recovery(self):
        from app.services.stream.agent_loop_driver import _run_round

        captured = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs)
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="请求失败。",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                output_deferred=kwargs.get("defer_output", False),
            )

        state = AgentLoopState()
        state.record_tool_outcome("mcp_lookup", "failed")
        result = await _run_round(
            messages=[{"role": "user", "content": "查询资料"}],
            state=state,
            runtime=_runtime(plan_mode="off", run_round_fn=run_round_fn),
            step_number=2,
            step_context=_step_context(),
        )
        self.assertTrue(result.output_deferred)
        self.assertTrue(captured[0]["defer_output"])

    async def test_generic_mcp_and_web_failures_are_incomplete(self):
        state = AgentLoopState()
        state.record_tool_outcome("mcp_lookup", "failed")
        state.record_tool_outcome("web_search", "failed")
        request = self.request(state, tools=("mcp_lookup", "web_search"))
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertTrue(state.unknown_terminated)

    async def test_failed_weather_then_real_search_content_keeps_cited_answer(self):
        from dataclasses import replace

        from app.schemas.chat import SearchBlock, SourceReference
        from app.services.tool_handlers.base import ToolResult

        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        state.record_tool_outcome("web_search", "success")
        url = "https://www.hko.gov.hk/wxinfo/currwx/fndc.htm"
        state.recovery_evidence.record_result(
            "web_search",
            ToolResult(status="success", data={"sources": [{"url": url, "description": "未来三天有骤雨。"}]}),
        )
        state.content_blocks.append(
            SearchBlock(
                type="search",
                query="香港三天天气",
                sources=[],
                source_refs=[SourceReference(kind="search", url=url, citation_index=1)],
            )
        )
        request = self.request(state)
        answer = "香港未来三天有骤雨，具体时段仍需关注更新。[1]"
        request = replace(request, round_result=replace(request.round_result, content_buf=answer))
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()) as append:
            await handle_agent_round_outcome(request=request)
        self.assertFalse(state.unknown_terminated)
        self.assertFalse(state.tool_recovery_prompted)
        self.assertIn(answer, str(append.await_args_list))
        self.assertNotIn("航班", str(append.await_args_list))
