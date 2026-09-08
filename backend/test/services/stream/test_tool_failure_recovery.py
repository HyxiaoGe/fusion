"""失败后的恢复必须跨过模型过早 stop，而不是返回假完成。"""

import unittest
from dataclasses import replace
from itertools import permutations
from unittest.mock import AsyncMock, patch

from app.schemas.chat import PlaceResult, PlaceResultsBlock, SearchBlock, SourceReference, Usage
from app.services.knowledge.chat_grounding import KNOWLEDGE_UNVERIFIABLE_ANSWER_TEXT
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

    async def test_recovery_stop_after_one_prompt_is_incomplete_even_with_unused_alternative(self):
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        request = self.request(state)
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            self.assertIsNone(await handle_agent_round_outcome(request=request))
            self.assertIsNotNone(await handle_agent_round_outcome(request=request))
        self.assertTrue(state.unknown_terminated)
        self.assertIn("未完成", state.content_blocks[-1].text)

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
                    with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
                        await handle_agent_round_outcome(request=request)
                    self.assertFalse(state.unknown_terminated)
                    self.assertFalse(state.tool_recovery_prompted)
                    self.assertEqual(state.content_blocks[-1].text, answer)

    async def test_failed_domain_tool_with_real_web_evidence_keeps_uncited_or_unknown_citation_answer(self):
        for suffix in ("", "[999]"):
            with self.subTest(suffix=suffix):
                state = AgentLoopState(product_tool_attempted=True)
                state.record_tool_outcome("weather_forecast", "failed")
                self.record_search(state)
                request = self.request(state)
                answer = f"香港未来三天有骤雨，具体时段仍需关注更新。{suffix}"
                request = replace(request, round_result=replace(request.round_result, content_buf=answer))
                with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
                    await handle_agent_round_outcome(request=request)
                self.assertFalse(state.unknown_terminated)
                self.assertFalse(state.tool_recovery_prompted)
                self.assertEqual(state.content_blocks[-1].text, answer)

    async def test_success_metadata_does_not_erase_failure_or_degradation_without_evidence(self):
        for issue_status in ("failed", "degraded"):
            for statuses in ((issue_status, "success"), ("success", issue_status)):
                with self.subTest(statuses=statuses):
                    state = AgentLoopState()
                    self.record_search(state, description="  ")
                    for status in statuses:
                        state.record_tool_outcome("web_search", status)
                    request = self.request(state, tools=("web_search",))
                    with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
                        await handle_agent_round_outcome(request=request)
                    self.assertTrue(state.unknown_terminated)
                    self.assertIn("未完成", state.content_blocks[-1].text)

    async def test_web_recovery_cannot_bypass_knowledge_evidence_contract(self):
        state = AgentLoopState()
        state.record_tool_outcome("mcp_lookup", "failed")
        self.record_search(state)
        request = self.request(state)
        request = replace(
            request,
            runtime=replace(request.runtime, evidence_policy="knowledge_grounded_v1"),
            round_result=replace(request.round_result, content_buf="香港未来三天有骤雨。"),
        )
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertFalse(state.tool_recovery_prompted)
        self.assertEqual(state.content_blocks[-1].text, KNOWLEDGE_UNVERIFIABLE_ANSWER_TEXT)

    async def test_web_recovery_cannot_bypass_successful_product_result_contract(self):
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("url_read", "degraded")
        self.record_search(state)
        state.content_blocks.append(
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="咖啡",
                status="success",
                result_count=1,
                places=[PlaceResult(name="示例咖啡")],
                limitations=["不包含实时排队或空位信息"],
            )
        )
        request = self.request(state)
        request = replace(
            request,
            messages=[{"role": "user", "content": "附近咖啡馆有空位吗"}],
            round_result=replace(request.round_result, content_buf="示例咖啡现在无需排队，保证有空位。"),
        )
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertIn("示例咖啡", state.content_blocks[-1].text)
        self.assertNotIn("保证有空位", state.content_blocks[-1].text)
        self.assertFalse(state.tool_recovery_prompted)

    async def test_web_recovery_cannot_bypass_deep_research_completion_contract(self):
        state = AgentLoopState()
        state.record_tool_outcome("url_read", "degraded")
        self.record_search(state)
        state.configure_research_mode(network_required=True)
        request = self.request(state)
        request = replace(
            request,
            runtime=replace(request.runtime, task_mode="deep_research", evidence_policy="deep_research_v1"),
            round_result=replace(request.round_result, content_buf="香港未来三天有骤雨。"),
        )
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            self.assertIsNone(await handle_agent_round_outcome(request=request))
        self.assertFalse(state.tool_recovery_prompted)
        self.assertEqual(state.research_repair_attempts, 1)
        self.assertFalse(any(block.type == "text" for block in state.content_blocks))

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

    async def test_successful_mcp_retry_keeps_answer_without_forcing_another_web_recovery(self):
        state = AgentLoopState()
        state.record_tool_outcome("mcp_lookup", "failed")
        state.record_tool_outcome("mcp_lookup", "success")
        request = self.request(state, tools=("mcp_lookup", "web_search", "url_read"))
        answer = "查询到匹配记录：编号A-123。"
        request = replace(request, round_result=replace(request.round_result, content_buf=answer))
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)
        self.assertFalse(state.tool_recovery_prompted)
        self.assertFalse(state.unknown_terminated)
        self.assertEqual(state.content_blocks[-1].text, answer)

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
