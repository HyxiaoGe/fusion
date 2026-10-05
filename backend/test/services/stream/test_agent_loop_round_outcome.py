import asyncio
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai.prompts.section_ids import RESEARCH_COMPLETION_REPAIR
from app.schemas.chat import (
    PlaceResult,
    PlaceResultsBlock,
    SearchBlock,
    SearchSourceSummary,
    SourceReference,
    Usage,
    WeatherForecastDay,
    WeatherResultsBlock,
)
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_outcome import AgentLoopExit
from app.services.stream.agent_loop_policy import AgentLoopLimits
from app.services.stream.agent_loop_round_outcome import (
    AgentRoundOutcomeRequest,
)
from app.services.stream.agent_loop_round_outcome import (
    handle_agent_round_outcome as _handle_round_outcome_unbound,
)
from app.services.stream.agent_loop_runtime import AgentLoopRuntime
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_round import ToolRoundOutcome


async def handle_agent_round_outcome(*, request: AgentRoundOutcomeRequest):
    """按 run_agent_loop 的真实接线绑定 step 完成，current_step_id 只随 step 完成释放。"""
    runtime = replace(
        request.runtime,
        complete_step_fn=request.state.bind_step_completion(request.runtime.complete_step_fn),
    )
    return await _handle_round_outcome_unbound(request=replace(request, runtime=runtime))


async def _complete_step_noop(**_kwargs):
    return 0


async def _complete_tool_round_step(kwargs):
    """模拟真实工具回合：返回前完成本 step。"""
    request = kwargs["request"]
    await request.complete_step_fn(context=request.step_context)


async def _unused_async(**_kwargs):
    raise AssertionError("不应调用这个依赖")


def _unused_sync(*_args, **_kwargs):
    raise AssertionError("不应调用这个依赖")


def _runtime(**overrides):
    values = {
        "conversation_id": "conv-outcome",
        "task_id": "task-outcome",
        "run_id": "run-outcome",
        "user_id": "user-outcome",
        "model_id": "gpt-4",
        "provider": "openai",
        "litellm_model": "openai/gpt-4",
        "litellm_kwargs": {},
        "should_use_reasoning": True,
        "call_kwargs": {},
        "assistant_message_id": "msg-outcome",
        "run_start": 0.0,
        "limits": AgentLoopLimits(max_steps=8, max_tool_calls=20, total_timeout_s=300),
        "emitter": object(),
        "session_cache": object(),
        "network_budget": object(),
        "start_step_fn": _unused_async,
        "complete_step_fn": _unused_async,
        "run_round_fn": _unused_async,
        "handle_tool_calls_round_fn": _unused_async,
        "run_limit_summary_step_fn": _unused_async,
        "llm_call_fn": _unused_async,
        "stream_round_fn": _unused_async,
        "execute_tools_fn": _unused_async,
        "persist_message_fn": _unused_sync,
        "log_round_summary_fn": lambda **_kwargs: None,
        "warning_fn": lambda _message: None,
        "clock": lambda: 1.0,
    }
    values.update(overrides)
    return AgentLoopRuntime(**values)


def _step_context(step_id="step-outcome"):
    return AgentStepContext(
        step_id=step_id,
        step_number=1,
        started_at=1.0,
        thinking_block_id=f"{step_id}-thinking",
        text_block_id=f"{step_id}-text",
    )


class AgentLoopRoundOutcomeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _deferred_lifecycle_request(lifecycle) -> AgentRoundOutcomeRequest:
        return AgentRoundOutcomeRequest(
            db="db",
            messages=[{"role": "user", "content": "测试 deferred commit"}],
            state=AgentLoopState(),
            runtime=_runtime(),
            step_number=1,
            step_context=_step_context("step-deferred-terminal"),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="候选答案",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=1, output_tokens=1),
                output_deferred=True,
                llm_lifecycle=lifecycle,
            ),
        )

    async def test_deferred_outcome_terminal_secondary_preserves_primary_base_exception(self):
        error_factories = (
            ("runtime", lambda role: RuntimeError(f"{role} secret")),
            ("cancel", lambda role: asyncio.CancelledError(f"{role} secret")),
        )

        for primary_name, make_primary in error_factories:
            for secondary_name, make_secondary in error_factories:
                with self.subTest(primary=primary_name, secondary=secondary_name):
                    primary = make_primary("primary")
                    secondary = make_secondary("secondary")
                    finish_success = AsyncMock(side_effect=secondary)
                    lifecycle = SimpleNamespace(finish_success=finish_success)
                    warning = Mock()
                    cancelling_before = asyncio.current_task().cancelling()

                    with (
                        patch(
                            "app.services.stream.agent_loop_round_outcome._handle_agent_round_outcome",
                            new=AsyncMock(side_effect=primary),
                        ),
                        patch(
                            "app.services.stream.agent_loop_round_outcome.logger",
                            new=SimpleNamespace(warning=warning),
                            create=True,
                        ),
                    ):
                        with self.assertRaises(type(primary)) as raised:
                            await handle_agent_round_outcome(
                                request=self._deferred_lifecycle_request(lifecycle),
                            )

                    self.assertIs(raised.exception, primary)
                    self.assertEqual(asyncio.current_task().cancelling(), cancelling_before)
                    finish_success.assert_awaited_once_with(output_visible=False)
                    warning.assert_called_once()
                    logged = repr(warning.call_args)
                    self.assertIn("error_code=deferred_terminal_failure", logged)
                    self.assertIn(type(secondary).__name__, logged)
                    self.assertNotIn(str(primary), logged)
                    self.assertNotIn(str(secondary), logged)

    async def test_deferred_outcome_terminal_failure_without_primary_remains_fail_closed(self):
        for secondary in (RuntimeError("secondary secret"), asyncio.CancelledError("secondary secret")):
            with self.subTest(secondary=type(secondary).__name__):
                finish_success = AsyncMock(side_effect=secondary)
                lifecycle = SimpleNamespace(finish_success=finish_success)
                warning = Mock()
                cancelling_before = asyncio.current_task().cancelling()

                with (
                    patch(
                        "app.services.stream.agent_loop_round_outcome._handle_agent_round_outcome",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "app.services.stream.agent_loop_round_outcome.logger",
                        new=SimpleNamespace(warning=warning),
                        create=True,
                    ),
                ):
                    with self.assertRaises(type(secondary)) as raised:
                        await handle_agent_round_outcome(
                            request=self._deferred_lifecycle_request(lifecycle),
                        )

                self.assertIs(raised.exception, secondary)
                self.assertEqual(asyncio.current_task().cancelling(), cancelling_before)
                finish_success.assert_awaited_once_with(output_visible=False)
                warning.assert_not_called()

    async def test_deep_synthesis_unannounced_tool_protocol_goes_directly_to_safe_summary(self):
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-synthesis", mode="on"))
        state.configure_research_mode(network_required=True)
        state.research_workset.successful_searches = 1
        state.research_workset.successful_read_urls = {
            "https://example.com/a",
            "https://example.com/b",
        }
        state.plan_coordinator.source = "model"
        state.plan_coordinator.revision = 1
        state.plan_coordinator.items = [
            {
                "id": "answer",
                "title": "综合回答",
                "status": "running",
                "kind": "answer",
                "depends_on": [],
                "planned_tools": [],
            }
        ]
        state.mark_current_step("step-synthesis")
        complete_step = AsyncMock()
        warnings = []

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "深度调研"}],
                state=state,
                runtime=_runtime(
                    complete_step_fn=complete_step,
                    task_mode="deep_research",
                    evidence_policy="deep_research_v1",
                    plan_mode="on",
                    warning_fn=warnings.append,
                ),
                step_number=4,
                step_context=_step_context("step-synthesis"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="",
                    tool_calls=[
                        {
                            "id": "dsml-step-synthesis-1",
                            "name": "update_plan",
                            "arguments": "{}",
                        }
                    ],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                    announced_tool_names=frozenset(),
                    output_deferred=True,
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.SUMMARY_REQUIRED)
        self.assertEqual(outcome.summary_finish_reason, "research_evidence_repair_exhausted")
        self.assertEqual(state.total_tool_calls, 0)
        self.assertIsNone(state.current_step_id)
        complete_step.assert_awaited_once()
        self.assertTrue(any("未公告工具协议" in warning for warning in warnings))

    async def test_deep_research_with_files_still_requires_network_evidence(self):
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-file", mode="on"))
        state.configure_research_mode(network_required=True)
        state.plan_coordinator.source = "model"
        state.plan_coordinator.revision = 1
        state.plan_coordinator.items = [{"id": "file", "status": "running"}]
        state.mark_current_step("step-file")
        messages = [{"role": "user", "content": "总结附件"}]

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=messages,
                state=state,
                runtime=_runtime(
                    complete_step_fn=AsyncMock(),
                    task_mode="deep_research",
                    evidence_policy="deep_research_v1",
                    plan_mode="on",
                ),
                step_number=1,
                step_context=_step_context("step-file"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="附件的核心结论如下。",
                    tool_calls=[],
                    finish_reason="stop",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                    output_deferred=True,
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertEqual(state.content_blocks, [])
        self.assertIn("Complete at least one valid search", messages[-1]["content"])
        self.assertEqual(messages[-1].section_id, RESEARCH_COMPLETION_REPAIR)

    async def test_plan_gate_does_not_discard_answering_block_that_was_never_streamed(self):
        emitter = AsyncMock()
        handle_tool_calls = AsyncMock(
            return_value=ToolRoundOutcome(
                tool_call_count=0,
                tool_names=[],
                product_result_count=0,
            )
        )
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-plan", mode="on"))

        await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "制定计划"}],
                state=state,
                runtime=_runtime(
                    emitter=emitter,
                    handle_tool_calls_round_fn=handle_tool_calls,
                    plan_mode="on",
                ),
                step_number=1,
                step_context=_step_context("step-plan-tool"),
                round_result=AgentRoundResult(
                    reasoning_buf="正在创建计划。",
                    content_buf="过程性正文",
                    tool_calls=[
                        {
                            "id": "call-plan",
                            "name": "update_plan",
                            "arguments": '{"plan":[]}',
                        }
                    ],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                    output_deferred=True,
                    allow_deferred_reasoning_output=True,
                ),
            )
        )

        emitter.content_block_discarded.assert_not_awaited()

    async def test_product_tool_round_returns_to_driver_for_next_model_decision(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-tool")
        state.consecutive_no_progress_search_results = 1
        tool_call = {"id": "tc-place", "name": "local_place_search", "arguments": '{"query":"咖啡"}'}

        async def handle_tool_calls_round_fn(**kwargs):
            await _complete_tool_round_step(kwargs)
            request = kwargs["request"]
            request.on_tools_executed(1)
            return ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["local_place_search"],
                no_progress_search_results=(True,),
                product_result_count=1,
            )

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "咖啡店和附近桌球"}],
                state=state,
                runtime=_runtime(
                    complete_step_fn=_complete_step_noop, handle_tool_calls_round_fn=handle_tool_calls_round_fn
                ),
                step_number=1,
                step_context=_step_context("step-product-tool"),
                round_result=AgentRoundResult(
                    reasoning_buf="先查咖啡店",
                    content_buf="",
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertEqual(state.total_tool_calls, 1)
        self.assertEqual(state.consecutive_no_progress_search_results, 2)
        self.assertIsNone(state.current_step_id)

    async def test_failed_product_tool_attempt_is_recorded_for_next_round_guard(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-failed")

        async def handle_tool_calls_round_fn(**kwargs):
            kwargs["request"].on_tools_executed(1)
            return ToolRoundOutcome(
                tool_call_count=1,
                tool_names=[],
                product_result_count=0,
            )

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "比较通勤路线"}],
                state=state,
                runtime=_runtime(handle_tool_calls_round_fn=handle_tool_calls_round_fn),
                step_number=1,
                step_context=_step_context("step-product-failed"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="",
                    tool_calls=[{"id": "tc-route", "name": "route_compare", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertTrue(state.product_tool_attempted)

    async def test_required_user_input_stops_before_another_model_round(self):
        state = AgentLoopState()
        state.mark_current_step("step-weather-ambiguous")

        async def handle_tool_calls_round_fn(**kwargs):
            await _complete_tool_round_step(kwargs)
            request = kwargs["request"]
            request.on_tools_executed(1)
            request.agent_state.pending_tool_repairs["repair-weather"] = {
                "required_fields": ["location"],
                "retryable": False,
                "requires_user_input": True,
            }
            return ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["weather_forecast"],
                product_result_count=0,
            )

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "南山区明天天气如何？"}],
                state=state,
                runtime=_runtime(
                    complete_step_fn=_complete_step_noop, handle_tool_calls_round_fn=handle_tool_calls_round_fn
                ),
                step_number=1,
                step_context=_step_context("step-weather-ambiguous"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="",
                    tool_calls=[
                        {
                            "id": "tc-weather",
                            "name": "weather_forecast",
                            "arguments": '{"location":"南山区"}',
                        }
                    ],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.PRODUCT_RESULT_READY)
        self.assertEqual(state.total_tool_calls, 1)
        self.assertIsNone(state.current_step_id)

    async def test_failed_travel_tool_attempt_is_recorded_for_next_round_guard(self):
        state = AgentLoopState()
        state.mark_current_step("step-travel-failed")

        async def handle_tool_calls_round_fn(**kwargs):
            kwargs["request"].on_tools_executed(1)
            return ToolRoundOutcome(tool_call_count=1, tool_names=[], product_result_count=0)

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "查询航班"}],
                state=state,
                runtime=_runtime(handle_tool_calls_round_fn=handle_tool_calls_round_fn),
                step_number=1,
                step_context=_step_context("step-travel-failed"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="",
                    tool_calls=[{"id": "tc-flight", "name": "search_flights", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertTrue(state.product_tool_attempted)

    async def test_empty_deferred_model_answer_still_completes_from_product_result(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-empty")
        state.content_blocks.append(
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="咖啡",
                status="success",
                result_count=1,
                places=[PlaceResult(name="示例咖啡")],
            )
        )
        append_chunk = AsyncMock()

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "附近咖啡"}],
                    state=state,
                    runtime=_runtime(complete_step_fn=AsyncMock()),
                    step_number=2,
                    step_context=_step_context("step-product-empty"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=0),
                        output_deferred=True,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertIn("示例咖啡", append_chunk.await_args.args[2])

    async def test_deferred_model_answer_is_delivered_as_is_after_product_tool_failure(self):
        model_answer = "地图查询这次失败了，我没法确认具体路线；你可以换个更具体的地点再问我。"
        for product_result in (False, True):
            with self.subTest(product_result=product_result):
                state = AgentLoopState(product_tool_attempted=True)
                state.record_tool_outcome("route_compare", "failed")
                if product_result:
                    state.content_blocks.append(
                        PlaceResultsBlock(
                            type="place_results",
                            schema_version=1,
                            provider="amap",
                            query="咖啡",
                            status="success",
                            result_count=1,
                            places=[PlaceResult(name="示例咖啡")],
                        )
                    )
                state.mark_current_step("step-product-failed")
                append_chunk = AsyncMock()

                with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
                    outcome = await handle_agent_round_outcome(
                        request=AgentRoundOutcomeRequest(
                            db="db",
                            messages=[{"role": "user", "content": "从虹桥站到外滩怎么走"}],
                            state=state,
                            runtime=_runtime(complete_step_fn=AsyncMock()),
                            step_number=2,
                            step_context=_step_context("step-product-failed"),
                            round_result=AgentRoundResult(
                                reasoning_buf="",
                                content_buf=model_answer,
                                tool_calls=[],
                                finish_reason="stop",
                                accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                                output_deferred=True,
                            ),
                        )
                    )

                self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
                self.assertEqual(append_chunk.await_args.args[2], model_answer)
                self.assertEqual(state.content_blocks[-1].text, model_answer)
                self.assertFalse(state.unknown_terminated)

    async def test_non_k3_deferred_answer_refresh_history_has_no_thinking_block(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-valid")
        state.content_blocks.append(
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="烤肉",
                near="深圳民治",
                status="success",
                result_count=1,
                places=[PlaceResult(name="炭火一号", rating=4.7)],
                limitations=["不包含实时排队或空位信息"],
            )
        )
        model_answer = (
            "结论：如果更看重本次返回的评分，可以优先看炭火一号。实时排队和空位本次无法确认，建议到店前核实。"
        )
        append_chunk = AsyncMock()
        complete_step_fn = AsyncMock()
        warnings: list[str] = []
        llm_lifecycle = AsyncMock()
        llm_lifecycle.record_output = Mock()

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "找一家烤肉店"}],
                    state=state,
                    runtime=_runtime(
                        complete_step_fn=complete_step_fn,
                        warning_fn=warnings.append,
                    ),
                    step_number=2,
                    step_context=_step_context("step-product-valid"),
                    round_result=AgentRoundResult(
                        reasoning_buf="只按实际字段总结",
                        content_buf=model_answer,
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=20),
                        output_deferred=True,
                        llm_lifecycle=llm_lifecycle,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(append_chunk.await_args.args[2], model_answer)
        self.assertEqual(state.content_blocks[-1].text, model_answer)
        self.assertEqual(
            [block.type for block in state.content_blocks],
            ["place_results", "text"],
        )
        self.assertEqual(warnings, [])
        complete_step_fn.assert_awaited_once()
        llm_lifecycle.publish_visible_output.assert_awaited_once_with("content")
        llm_lifecycle.finish_success.assert_awaited_once_with(output_visible=False)

    async def test_valid_deferred_product_answer_preserves_brand_text_in_stream_and_storage(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-provider-neutral")
        state.content_blocks.append(
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="商场",
                near="深圳市民中心",
                status="success",
                result_count=1,
                places=[PlaceResult(name="高德置地广场")],
            )
        )
        append_chunk = AsyncMock()

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "附近有什么商场"}],
                    state=state,
                    runtime=_runtime(complete_step_fn=AsyncMock()),
                    step_number=2,
                    step_context=_step_context("step-product-provider-neutral"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="根据高德返回的结果，可以优先查看高德置地广场。",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=12),
                        output_deferred=True,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        emitted_answer = append_chunk.await_args.args[2]
        self.assertEqual(emitted_answer, "根据高德返回的结果，可以优先查看高德置地广场。")
        self.assertEqual(state.content_blocks[-1].text, emitted_answer)

    async def test_cancelled_deferred_model_output_is_not_persisted(self):
        state = AgentLoopState()
        state.mark_current_step("step-product-cancelled")

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "路线"}],
                state=state,
                runtime=_runtime(),
                step_number=2,
                step_context=_step_context("step-product-cancelled"),
                round_result=AgentRoundResult(
                    reasoning_buf="准备补充停车建议",
                    content_buf="停车方便",
                    tool_calls=[],
                    finish_reason="cancelled",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=1),
                    output_deferred=True,
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.SUPERSEDED)
        self.assertEqual(state.content_blocks, [])

    async def test_deferred_weather_answer_uses_validated_model_candidate(self):
        state = AgentLoopState()
        state.mark_current_step("step-weather-activity-repair")
        state.content_blocks.append(
            WeatherResultsBlock(
                type="weather_results",
                schema_version=1,
                provider="amap",
                status="degraded",
                query="南山区",
                resolved_location="南山区",
                day_count=1,
                forecast_days=[
                    WeatherForecastDay(
                        date=date(2026, 7, 24),
                        weekday=5,
                        day_weather="雷阵雨",
                        night_weather="多云",
                        high_c=31,
                        low_c=26,
                    )
                ],
                fetched_at=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
                limitations=["天气预报按行政区提供，不代表具体建筑物"],
            )
        )
        model_answer = (
            "7月24日（周五）南山区白天雷阵雨、夜间多云，26–31℃。"
            "本次预报只有白天和夜间粒度，无法确认上午是否下雨，也不能据此判断上午骑行能否避雨。"
        )
        append_chunk = AsyncMock()
        warnings: list[str] = []

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "7月24日南山区天气怎么样，适合上午骑行吗？"}],
                    state=state,
                    runtime=_runtime(complete_step_fn=AsyncMock(), warning_fn=warnings.append),
                    step_number=2,
                    step_context=_step_context("step-weather-activity-repair"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf=model_answer,
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=30),
                        output_deferred=True,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        emitted_answer = append_chunk.await_args.args[2]
        self.assertEqual(emitted_answer, model_answer)
        self.assertEqual(state.content_blocks[-1].text, emitted_answer)
        self.assertEqual(warnings, [])

    async def test_deferred_mixed_travel_answer_uses_validated_model_candidate(self):
        state = AgentLoopState()
        state.mark_current_step("step-mixed-travel-answer")
        state.content_blocks.extend(
            [
                {
                    "type": "flight_results",
                    "status": "success",
                    "id": "flight-out",
                    "origin": "北京",
                    "destination": "上海",
                    "departure_date": "2026-08-29",
                    "flights": [
                        {
                            "id": "flight-1",
                            "flight_no": "MU5101",
                            "duration_s": 8100,
                            "price": {"currency": "CNY", "amount_minor": 76000},
                            "departure": {"station_name": "北京首都国际机场", "scheduled_at": "2026-08-29T07:00:00"},
                            "arrival": {"station_name": "上海浦东国际机场", "scheduled_at": "2026-08-29T09:15:00"},
                        }
                    ],
                    "limitations": ["班次与参考价格仅代表本次查询时刻"],
                },
                {
                    "type": "train_results",
                    "status": "success",
                    "id": "train-out",
                    "origin": "北京",
                    "destination": "上海",
                    "departure_date": "2026-08-29",
                    "trains": [
                        {
                            "id": "train-1",
                            "train_no": "G1",
                            "duration_s": 17640,
                            "price": {"currency": "CNY", "amount_minor": 66100},
                            "departure": {"station_name": "北京南站", "scheduled_at": "2026-08-29T06:30:00"},
                            "arrival": {"station_name": "上海虹桥站", "scheduled_at": "2026-08-29T11:24:00"},
                        }
                    ],
                    "limitations": ["本次结果不包含余票或准点率"],
                },
            ]
        )
        model_answer = "本次返回候选中，G1 参考价 661 元低于 MU5101 的 760 元；MU5101 计划用时比 G1 短。"
        append_chunk = AsyncMock()
        warnings: list[str] = []

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "北京到上海，高铁和飞机都查，比较最省钱和最快方案"}],
                    state=state,
                    runtime=_runtime(complete_step_fn=AsyncMock(), warning_fn=warnings.append),
                    step_number=3,
                    step_context=_step_context("step-mixed-travel-answer"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf=model_answer,
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=30),
                        output_deferred=True,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        emitted_answer = append_chunk.await_args.args[2]
        self.assertEqual(emitted_answer, model_answer)
        self.assertEqual(state.content_blocks[-1].text, emitted_answer)
        self.assertEqual(warnings, [])

    async def test_deferred_single_train_comparison_uses_validated_model_candidate(self):
        state = AgentLoopState()
        state.mark_current_step("step-single-train-comparison")
        state.content_blocks.append(
            {
                "type": "train_results",
                "status": "success",
                "id": "train-out",
                "origin": "北京",
                "destination": "上海",
                "departure_date": "2026-08-29",
                "trains": [
                    {
                        "train_no": "G737",
                        "duration_s": 21900,
                        "price": {"currency": "CNY", "amount_minor": 59800},
                    },
                    {
                        "train_no": "G37",
                        "duration_s": 16980,
                        "price": {"currency": "CNY", "amount_minor": 66100},
                    },
                ],
                "limitations": ["班次与参考价格仅代表本次查询时刻"],
            }
        )
        append_chunk = AsyncMock()
        warnings: list[str] = []
        model_answer = "本次返回候选中，G737 参考价 598 元最低，G37 计划用时 4 小时 43 分钟最短。"

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", append_chunk):
            outcome = await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[
                        {
                            "role": "user",
                            "content": "请查询北京到上海的高铁，告诉我本次返回中最便宜和最快的车次。",
                        }
                    ],
                    state=state,
                    runtime=_runtime(complete_step_fn=AsyncMock(), warning_fn=warnings.append),
                    step_number=2,
                    step_context=_step_context("step-single-train-comparison"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf=model_answer,
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=2, output_tokens=10),
                        output_deferred=True,
                    ),
                )
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        emitted_answer = append_chunk.await_args.args[2]
        self.assertEqual(emitted_answer, model_answer)
        self.assertEqual(state.content_blocks[-1].text, emitted_answer)
        self.assertEqual(warnings, [])

    async def test_final_answer_evidence_does_not_swallow_stream_write_unavailable(self):
        from app.services.stream_state_service import StreamWriteUnavailableError

        state = AgentLoopState()
        state.mark_current_step("step-write-failed")
        state.content_blocks.append(
            SearchBlock(
                type="search",
                id="blk-search",
                query="Redis",
                sources=[SearchSourceSummary(title="官方文档", url="https://redis.io/docs")],
                source_refs=[SourceReference(kind="search", title="官方文档", url="https://redis.io/docs")],
                source_count=1,
            )
        )
        emitter = SimpleNamespace(
            evidence_item_upserted=AsyncMock(side_effect=StreamWriteUnavailableError("Redis write failed"))
        )

        with self.assertRaises(StreamWriteUnavailableError):
            await handle_agent_round_outcome(
                request=AgentRoundOutcomeRequest(
                    db="db",
                    messages=[{"role": "user", "content": "hi"}],
                    state=state,
                    runtime=_runtime(emitter=emitter, complete_step_fn=AsyncMock()),
                    step_number=1,
                    step_context=_step_context("step-write-failed"),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="参考官方文档。[1]",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                    ),
                )
            )

    async def test_stop_round_appends_blocks_completes_step_and_returns_completed(self):
        state = AgentLoopState()
        state.mark_current_step("step-stop")
        completed_steps = []
        step_context = _step_context("step-stop")

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(complete_step_fn=complete_step_fn),
                step_number=1,
                step_context=step_context,
                round_result=AgentRoundResult(
                    reasoning_buf="思考",
                    content_buf="回答",
                    tool_calls=[],
                    finish_reason="stop",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(completed_steps, ["step-stop"])
        self.assertEqual(state.current_step_id, None)
        self.assertEqual([block.type for block in state.content_blocks], ["thinking", "text"])

    async def test_stop_round_marks_final_answer_used_evidence_before_completion(self):
        state = AgentLoopState()
        state.mark_current_step("step-used")
        step_context = _step_context("step-used")
        state.content_blocks.append(
            SearchBlock(
                type="search",
                id="blk-search",
                query="OpenAI 产品更新",
                sources=[
                    SearchSourceSummary(title="官方公告", url="https://openai.com/news/product"),
                    SearchSourceSummary(title="媒体报道", url="https://example.com/media"),
                ],
                source_refs=[
                    SourceReference(kind="search", title="官方公告", url="https://openai.com/news/product"),
                    SourceReference(kind="search", title="媒体报道", url="https://example.com/media"),
                ],
                source_count=2,
            )
        )
        emitter = SimpleNamespace(evidence_item_upserted=AsyncMock())
        calls = []

        async def complete_step_fn(**kwargs):
            calls.append(("complete", kwargs["context"].step_id))

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(emitter=emitter, complete_step_fn=complete_step_fn),
                step_number=1,
                step_context=step_context,
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="最终回答使用官方公告。[1]",
                    tool_calls=[],
                    finish_reason="stop",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(calls, [("complete", "step-used")])
        emitter.evidence_item_upserted.assert_awaited_once()
        event = emitter.evidence_item_upserted.await_args.kwargs
        self.assertIsNone(event["tool_call_id"])
        self.assertEqual(event["evidence"]["status"], "used")
        self.assertTrue(event["evidence"]["used_by_final_answer"])
        self.assertEqual(event["evidence"]["url"], "https://openai.com/news/product")

    async def test_cancelled_round_appends_partial_blocks_and_returns_superseded(self):
        state = AgentLoopState()
        state.mark_current_step("step-cancelled")
        step_context = _step_context("step-cancelled")

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(),
                step_number=1,
                step_context=step_context,
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="半截回答",
                    tool_calls=[],
                    finish_reason="cancelled",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.SUPERSEDED)
        self.assertEqual(outcome.error_msg, "被新请求取代")
        self.assertEqual(state.current_step_id, "step-cancelled")
        self.assertEqual([block.type for block in state.content_blocks], ["text"])

    async def test_tool_calls_round_delegates_and_requests_loop_continue(self):
        state = AgentLoopState()
        state.mark_current_step("step-tool")
        messages = [{"role": "user", "content": "hi"}]
        tool_requests = []
        step_context = _step_context("step-tool")

        async def handle_tool_calls_round_fn(**kwargs):
            await _complete_tool_round_step(kwargs)
            tool_requests.append(kwargs["request"])
            kwargs["request"].on_tools_executed(len(kwargs["request"].tool_calls))

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=messages,
                state=state,
                runtime=_runtime(
                    complete_step_fn=_complete_step_noop, handle_tool_calls_round_fn=handle_tool_calls_round_fn
                ),
                step_number=1,
                step_context=step_context,
                round_result=AgentRoundResult(
                    reasoning_buf="需要工具",
                    content_buf="",
                    tool_calls=[{"id": "tc-1", "name": "web_search", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertEqual(state.total_tool_calls, 1)
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(tool_requests[0].db, "db")
        self.assertIs(tool_requests[0].messages, messages)

    async def test_tool_call_round_discards_streamed_preamble_before_execution(self):
        state = AgentLoopState()
        state.mark_current_step("step-tool-preamble")
        emitter = SimpleNamespace(content_block_discarded=AsyncMock())
        execution_started = False

        async def handle_tool_calls_round_fn(**kwargs):
            nonlocal execution_started
            self.assertEqual(emitter.content_block_discarded.await_count, 1)
            execution_started = True
            kwargs["request"].on_tools_executed(1)

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "比较通勤路线"}],
                state=state,
                runtime=_runtime(
                    emitter=emitter,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                ),
                step_number=1,
                step_context=_step_context("step-tool-preamble"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="好的，我先调用路线工具。",
                    tool_calls=[{"id": "tc-route", "name": "route_compare", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=8),
                ),
            )
        )

        self.assertIsNone(outcome)
        self.assertTrue(execution_started)
        emitter.content_block_discarded.assert_awaited_once_with(block_id="step-tool-preamble-text")

    async def test_plan_mode_tool_round_uses_deferred_content_without_discard_fallback(self):
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-deferred-tool", mode="on"))
        self.assertTrue(
            state.plan_coordinator.apply_model_update(
                {
                    "reason": "先查询再回答",
                    "items": [
                        {
                            "id": "route",
                            "title": "查询路线",
                            "status": "running",
                            "kind": "search",
                            "depends_on": [],
                            "planned_tools": ["route_compare"],
                        },
                        {
                            "id": "answer",
                            "title": "整理建议",
                            "status": "pending",
                            "kind": "answer",
                            "depends_on": ["route"],
                            "planned_tools": [],
                        },
                    ],
                }
            ).accepted
        )
        state.mark_current_step("step-deferred-tool")
        emitter = SimpleNamespace(content_block_discarded=AsyncMock())

        async def handle_tool_calls_round_fn(**kwargs):
            kwargs["request"].on_tools_executed(1)

        await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "继续查询"}],
                state=state,
                runtime=_runtime(
                    emitter=emitter,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                    plan_mode="on",
                ),
                step_number=2,
                step_context=_step_context("step-deferred-tool"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="继续查询。",
                    tool_calls=[{"id": "tc-route", "name": "route_compare", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=4),
                    output_deferred=True,
                ),
            )
        )

        emitter.content_block_discarded.assert_not_awaited()

    async def test_unknown_round_marks_unknown_and_completes_text_step(self):
        state = AgentLoopState()
        state.mark_current_step("step-unknown")
        completed_steps = []
        step_context = _step_context("step-unknown")

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)

        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(complete_step_fn=complete_step_fn),
                step_number=1,
                step_context=step_context,
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="退化回答",
                    tool_calls=[],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=1, output_tokens=2),
                ),
            )
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertTrue(state.unknown_terminated)
        self.assertEqual(completed_steps, ["step-unknown"])
        self.assertEqual(state.current_step_id, None)


if __name__ == "__main__":
    unittest.main()


def _document_plan_state(*, run_id: str, with_document: bool) -> AgentLoopState:
    coordinator = PlanCoordinator(run_id=run_id, mode="on")
    assert coordinator.apply_model_update(
        {
            "reason": "先查资料，再写文档",
            "items": [
                {
                    "id": "search",
                    "title": "查询资料",
                    "status": "pending",
                    "kind": "search",
                    "depends_on": [],
                    "planned_tools": ["web_search"],
                },
                {
                    "id": "doc",
                    "title": "撰写文档",
                    "status": "pending",
                    "kind": "answer",
                    "depends_on": ["search"],
                    "planned_tools": [],
                },
            ],
        }
    ).accepted
    state = AgentLoopState(plan_coordinator=coordinator)
    if with_document:
        state.content_blocks.append({"type": "document", "document_id": "doc-1"})
    state.mark_current_step(f"{run_id}-step")
    return state


def _has_section(messages, section_id: str) -> bool:
    return any(
        (message.get("section_id") if isinstance(message, dict) else getattr(message, "section_id", None)) == section_id
        for message in messages
    )


class DocumentDeliveredPendingPlanTests(unittest.IsolatedAsyncioTestCase):
    async def _stop_round(self, state: AgentLoopState, run_id: str):
        emitter = AsyncMock()
        messages = [{"role": "user", "content": "帮我做一份攻略"}]
        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db="db",
                messages=messages,
                state=state,
                runtime=_runtime(
                    complete_step_fn=_complete_step_noop,
                    emitter=emitter,
                    session_cache=AsyncMock(),
                    plan_mode="on",
                ),
                step_number=4,
                step_context=_step_context(f"{run_id}-step"),
                round_result=AgentRoundResult(
                    reasoning_buf="",
                    content_buf="攻略已生成。",
                    tool_calls=[],
                    finish_reason="stop",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                    announced_tool_names=frozenset({"web_search"}),
                    output_deferred=True,
                ),
            )
        )
        return outcome, emitter, messages
