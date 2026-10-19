"""工具失败后模型直接作答时原样交付，服务端不再注入补救提示让它重试。"""

import unittest
from dataclasses import replace
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.chat import SearchBlock, SourceReference, Usage
from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest, handle_agent_round_outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.tool_handlers.base import ToolResult
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class ToolFailureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def record_search(self, state, *, description="未来三天有骤雨。"):
        url = "https://www.hko.gov.hk/wxinfo/currwx/fndc.htm"
        state.record_tool_outcome("web_search", "success")
        state.recovery_evidence.record_result(
            "web_search",
            ToolResult(status="success", data={"sources": [{"url": url, "description": description}]}),
        )
        state.content_blocks.append(
            SearchBlock(
                type="search",
                query="香港三天天气",
                sources=[],
                source_refs=[SourceReference(kind="search", url=url, citation_index=1)],
            )
        )

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

    async def test_failed_tool_stop_delivers_model_answer_without_recovery_round(self):
        state = AgentLoopState()
        state.record_tool_outcome("weather_forecast", "failed")
        request = self.request(state)
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()) as append:
            outcome = await handle_agent_round_outcome(request=request)
        self.assertIsNotNone(outcome)
        self.assertIn("天气查询失败，请稍后再试。", str(append.await_args_list))
        self.assertEqual(request.messages[-1]["role"], "user")

    async def test_real_web_evidence_keeps_answer_across_outcome_orders_and_citation_styles(self):
        for statuses in permutations(("success", "degraded", "failed")):
            for suffix in ("", "[1]", "[999]"):
                with self.subTest(statuses=statuses, suffix=suffix):
                    state = AgentLoopState()
                    self.record_search(state)
                    for status in statuses:
                        state.record_tool_outcome("url_read", status)
                    request = self.request(state, tools=("web_search", "url_read"))
                    answer = f"香港未来三天有骤雨，具体时段仍需关注更新。{suffix}"
                    request = replace(request, round_result=replace(request.round_result, content_buf=answer))
                    with patch("app.services.stream_state_service.append_chunk", AsyncMock()):
                        await handle_agent_round_outcome(request=request)
                    self.assertFalse(state.unknown_terminated)
                    self.assertEqual(state.content_blocks[-1].text, answer)

    async def test_failed_domain_tool_with_real_web_evidence_keeps_uncited_or_unknown_citation_answer(self):
        for suffix in ("", "[999]"):
            with self.subTest(suffix=suffix):
                state = AgentLoopState()
                state.record_tool_outcome("weather_forecast", "failed")
                self.record_search(state)
                request = self.request(state)
                answer = f"香港未来三天有骤雨，具体时段仍需关注更新。{suffix}"
                request = replace(request, round_result=replace(request.round_result, content_buf=answer))
                with patch("app.services.stream_state_service.append_chunk", AsyncMock()):
                    await handle_agent_round_outcome(request=request)
                self.assertFalse(state.unknown_terminated)
                self.assertEqual(state.content_blocks[-1].text, answer)

    async def test_user_input_repair_adds_no_recovery_prompt(self):
        state = AgentLoopState()
        state.record_tool_outcome("weather_forecast", "failed")
        request = self.request(state)
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertEqual(request.messages[-1]["role"], "user")

    async def test_place_choice_adds_no_recovery_prompt(self):
        state = AgentLoopState()
        state.record_tool_outcome(
            "route_compare",
            "failed",
            place_choice={"place_name": "外滩", "candidates": [{"candidate": 1}, {"candidate": 2}]},
        )
        self.assertEqual(state.tool_issue_names, set())
        request = self.request(state)
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertEqual(request.messages[-1]["role"], "user")

    async def test_failed_mcp_round_streams_output(self):
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
        self.assertFalse(result.output_deferred)
        self.assertNotIn("defer_output", captured[0])

    async def test_successful_mcp_retry_keeps_answer_without_forcing_another_web_recovery(self):
        state = AgentLoopState()
        state.record_tool_outcome("mcp_lookup", "failed")
        state.record_tool_outcome("mcp_lookup", "success")
        request = self.request(state, tools=("mcp_lookup", "web_search", "url_read"))
        answer = "查询到匹配记录：编号A-123。"
        request = replace(request, round_result=replace(request.round_result, content_buf=answer))
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertFalse(state.unknown_terminated)
        self.assertEqual(state.content_blocks[-1].text, answer)

    async def test_failed_weather_then_real_search_content_keeps_cited_answer(self):
        from dataclasses import replace

        from app.schemas.chat import SearchBlock, SourceReference
        from app.services.tool_handlers.base import ToolResult

        state = AgentLoopState()
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
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()) as append:
            await handle_agent_round_outcome(request=request)
        self.assertFalse(state.unknown_terminated)
        self.assertIn(answer, str(append.await_args_list))
        self.assertNotIn("航班", str(append.await_args_list))


class MissingProductToolTests(unittest.IsolatedAsyncioTestCase):
    def request(self, state):
        return AgentRoundOutcomeRequest(
            db=None,
            messages=[{"role": "user", "content": "西安回民街附近有啥好吃的"}],
            state=state,
            runtime=_runtime(
                emitter=AsyncMock(),
                complete_step_fn=AsyncMock(),
                capability_resolution=SimpleNamespace(
                    external_tool_names=("local_place_search", "web_search", "url_read")
                ),
            ),
            step_number=1,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="回民街推荐老孙家泡馍。",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                context=None,
                output_deferred=True,
            ),
        )

    async def test_stop_without_product_tool_is_delivered_without_repair_round(self):
        request = self.request(AgentLoopState())
        with patch("app.services.stream_state_service.append_chunk", AsyncMock()) as append:
            await handle_agent_round_outcome(request=request)
        self.assertIn("回民街推荐老孙家泡馍。", str(append.await_args_list))
        self.assertEqual(request.messages[-1]["role"], "user")
