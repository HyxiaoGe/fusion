import asyncio
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from prometheus_client import REGISTRY

from app.ai.prompts.section_ids import RESEARCH_EVIDENCE_WORKSET
from app.schemas.chat import PlaceResult, PlaceResultsBlock, SourceReference, TextBlock, UrlBlock, Usage
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import AgentLoopExit, _run_limit_summary, _run_round, run_agent_loop
from app.services.stream.agent_loop_policy import AgentLoopLimits, AgentRunTerminalState, map_run_terminal_state
from app.services.stream.agent_loop_runtime import AgentLoopRuntime
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.first_output_timeout import (
    FALLBACK_FIRST_OUTPUT_TIMEOUT_S,
    FIRST_OUTPUT_TIMEOUT_S,
    FirstOutputTimeoutError,
)
from app.services.stream.limit_summary import LimitSummaryOutcome
from app.services.stream.product_structured_answers import PRODUCT_STRUCTURED_ANSWERS
from app.services.stream.research_workset_messages import messages_with_research_workset
from app.services.stream.run_output import ConversationStreamOutput
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_round import ToolRoundOutcome


@dataclass
class DummyEmitter:
    limit_reasons: list[str]
    plan_snapshots: list[dict] = field(default_factory=list)

    async def run_limit_reached(self, *, reason):
        self.limit_reasons.append(reason)

    async def plan_snapshot(self, **snapshot):
        self.plan_snapshots.append(snapshot)


async def _unused_async(**_kwargs):
    raise AssertionError("不应调用这个依赖")


def _unused_sync(*_args, **_kwargs):
    raise AssertionError("不应调用这个依赖")


async def _complete_step_noop(**_kwargs):
    return 0


def _runtime(**overrides):
    values = {
        "conversation_id": "conv-driver",
        "task_id": "task-driver",
        "run_id": "run-driver",
        "user_id": "user-driver",
        "model_id": "gpt-4",
        "provider": "openai",
        "litellm_model": "openai/gpt-4",
        "litellm_kwargs": {},
        "should_use_reasoning": True,
        "call_kwargs": {},
        "assistant_message_id": "msg-driver",
        "run_start": 0.0,
        "limits": AgentLoopLimits(max_steps=8, max_tool_calls=20, total_timeout_s=300),
        "emitter": DummyEmitter(limit_reasons=[]),
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
    values.setdefault("output", ConversationStreamOutput(values["conversation_id"], values["task_id"]))
    # 聊天入口装产品结构化回答，这里与之保持一致。
    values.setdefault("structured_answers", PRODUCT_STRUCTURED_ANSWERS)
    return AgentLoopRuntime(**values)


def _tool_definition(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _tool_names(call_kwargs: dict) -> list[str]:
    return [tool["function"]["name"] for tool in call_kwargs.get("tools", [])]


class AgentLoopDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_without_tool_attempt_streams_directly_with_or_without_plan_tool(self):
        for plan_mode in ("on", "off"):
            with self.subTest(plan_mode=plan_mode):
                observed = []

                async def run_round_fn(**kwargs):
                    observed.append(kwargs.get("defer_output"))
                    return AgentRoundResult(
                        reasoning_buf="",
                        content_buf="北京到上海坐高铁 4 小时。",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(input_tokens=1, output_tokens=1),
                        output_deferred=bool(kwargs.get("defer_output")),
                    )

                await _run_round(
                    messages=[{"role": "user", "content": "规划北京到上海的交通"}],
                    state=AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-no-product", mode=plan_mode)),
                    runtime=_runtime(
                        capability_resolution=SimpleNamespace(external_tool_names=("route_compare", "search_trains")),
                        call_kwargs={"tools": [_tool_definition("route_compare"), _tool_definition("search_trains")]},
                        run_round_fn=run_round_fn,
                    ),
                    step_number=1,
                    step_context=AgentStepContext(
                        step_id="step-no-product",
                        step_number=1,
                        started_at=1.0,
                        thinking_block_id="thinking-no-product",
                        text_block_id="text-no-product",
                    ),
                )

                # 服务端不再替换回答，模型没调工具时直接流式交付，不先缓存。
                self.assertEqual(observed, [None])

    async def _deliver_document_scenario(self, *, round_result, capability_resolution=None, content_blocks=()):
        state = AgentLoopState(content_blocks=list(content_blocks))
        rounds: list[dict] = []
        summaries: list[object] = []

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id=f"step-{kwargs['step_number']}",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{kwargs['step_number']}",
                text_block_id=f"text-{kwargs['step_number']}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            rounds.append(kwargs)
            return round_result

        async def handle_tool_calls_round_fn(*, request):
            request.on_tools_executed(1)
            request.agent_state.content_blocks.append({"type": "document", "document_id": "doc-1"})
            return ToolRoundOutcome(tool_call_count=1, tool_names=["create_document"])

        async def run_limit_summary_step_fn(*, request):
            summaries.append(request)
            return LimitSummaryOutcome(accumulated_usage=Usage(input_tokens=1, output_tokens=1))

        outcome = await _run_limit_summary(
            db=object(),
            state=state,
            runtime=_runtime(
                emitter=AsyncMock(),
                output_tool_names=frozenset({"create_document", "edit_document"}),
                capability_resolution=capability_resolution,
                call_kwargs={
                    "tools": [
                        _tool_definition("update_plan"),
                        _tool_definition("web_search"),
                        _tool_definition("create_document"),
                        _tool_definition("edit_document"),
                    ],
                    "tool_choice": "auto",
                },
                start_step_fn=start_step_fn,
                complete_step_fn=AsyncMock(),
                run_round_fn=run_round_fn,
                handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                run_limit_summary_step_fn=run_limit_summary_step_fn,
            ),
            messages=[{"role": "user", "content": "做一份香港三日攻略"}],
            summary_finish_reason="max_steps",
        )
        self.assertIsNone(outcome)
        return state, rounds, summaries

    async def test_document_round_skipped_when_document_already_written(self):
        state, rounds, summaries = await self._deliver_document_scenario(
            round_result=None,
            content_blocks=[{"type": "document", "document_id": "doc-0"}],
        )

        self.assertEqual(rounds, [])
        self.assertTrue(summaries[0].document_delivered)

    async def test_incomplete_standard_summary_marks_run_unknown_terminated(self):
        state = AgentLoopState()
        summary = AsyncMock(
            return_value=LimitSummaryOutcome(
                accumulated_usage=Usage(input_tokens=3, output_tokens=5),
                incomplete=True,
            )
        )

        await _run_limit_summary(
            state=state,
            runtime=_runtime(run_limit_summary_step_fn=summary),
            messages=[{"role": "user", "content": "分析问题"}],
            summary_finish_reason="limit_summary",
        )

        self.assertTrue(state.unknown_terminated)

    async def test_round_ignoring_tool_ban_is_discarded_and_redone_without_tools_or_history(self):
        state = AgentLoopState(history_tool_call_sequences={"history-call": 2})
        captured = []
        lifecycle = MagicMock()
        lifecycle.finish_success = AsyncMock()

        async def run_round_fn(**kwargs):
            captured.append(kwargs)
            if len(captured) == 1:
                return AgentRoundResult(
                    reasoning_buf="",
                    content_buf="",
                    tool_calls=[{"id": "c1", "name": "web_search", "arguments": "{}"}],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=5, output_tokens=1),
                    llm_lifecycle=lifecycle,
                )
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="答复",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=8, output_tokens=3),
            )

        history = [
            {"role": "user", "content": "q1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "history-call", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "history-call", "content": "旧结果"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        warnings = []
        with patch(
            "app.services.stream.agent_loop_driver._filter_exhausted_dynamic_tools",
            new=AsyncMock(return_value={}),
        ):
            result = await _run_round(
                messages=history,
                state=state,
                runtime=_runtime(
                    call_kwargs={"tools": [_tool_definition("web_search")], "tool_choice": "auto"},
                    run_round_fn=run_round_fn,
                    warning_fn=warnings.append,
                ),
                step_number=2,
                step_context=AgentStepContext(
                    step_id="step-ban",
                    step_number=2,
                    started_at=1.0,
                    thinking_block_id="thinking-ban",
                    text_block_id="text-ban",
                ),
            )

        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]["call_kwargs"]["tool_choice"], "none")
        self.assertIn("history-call", [message.get("tool_call_id") for message in captured[0]["messages"]])
        self.assertNotIn("tools", captured[1]["call_kwargs"])
        self.assertNotIn("tool_choice", captured[1]["call_kwargs"])
        self.assertNotIn("history-call", [message.get("tool_call_id") for message in captured[1]["messages"]])
        lifecycle.suppress_output.assert_called_once_with("tool_round")
        lifecycle.finish_success.assert_awaited_once_with(output_visible=False)
        self.assertEqual(result.content_buf, "答复")
        self.assertEqual(captured[1]["accumulated_usage"], Usage(input_tokens=5, output_tokens=1))
        self.assertEqual(len(warnings), 1)

    async def test_required_user_input_goes_back_to_model_for_clarification(self):
        state = AgentLoopState()
        started_steps: list[int] = []
        llm_steps: list[int] = []

        async def start_step_fn(**kwargs):
            started_steps.append(kwargs["step_number"])
            context = AgentStepContext(
                step_id=f"step-repair-{kwargs['step_number']}",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-repair-{kwargs['step_number']}",
                text_block_id=f"text-repair-{kwargs['step_number']}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            llm_steps.append(kwargs["step_number"])
            if len(llm_steps) > 1:
                return self._clarification_round_result()
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[
                    {
                        "id": "tc-weather-ambiguous",
                        "name": "weather_forecast",
                        "arguments": '{"location":"南山区"}',
                    }
                ],
                finish_reason="tool_calls",
                accumulated_usage=Usage(input_tokens=2, output_tokens=3),
            )

        async def handle_tool_calls_round_fn(**kwargs):
            # 工具结果带 repair.requires_user_input=true，由模型按工具说明直接问用户。
            request = kwargs["request"]
            request.on_tools_executed(1)
            return ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["weather_forecast"],
            )

        append_chunk = AsyncMock()
        with patch("app.services.stream_state_service.append_chunk", append_chunk):
            outcome = await run_agent_loop(
                db=object(),
                messages=[{"role": "user", "content": "南山区明天天气如何？"}],
                state=state,
                runtime=_runtime(
                    start_step_fn=start_step_fn,
                    complete_step_fn=AsyncMock(),
                    run_round_fn=run_round_fn,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                ),
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(llm_steps, [1, 2])
        self.assertEqual(started_steps, [1, 2])
        self.assertEqual(state.content_blocks[-1].text, self._clarification_round_result().content_buf)
        self.assertEqual(state.total_tool_calls, 1)

    async def _run_weather_tool_loop(self, run_round_fn, *, resolve_model, fallback="deepseek-chat"):
        state = AgentLoopState()
        tool_round_models: list[str] = []
        warnings: list[str] = []

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id=f"step-{kwargs['step_number']}",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{kwargs['step_number']}",
                text_block_id=f"text-{kwargs['step_number']}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def handle_tool_calls_round_fn(**kwargs):
            request = kwargs["request"]
            tool_round_models.append(request.model_id)
            request.on_tools_executed(1)
            return ToolRoundOutcome(tool_call_count=1, tool_names=["weather_forecast"])

        with (
            patch("app.services.stream_state_service.append_chunk", AsyncMock()),
            patch("app.services.stream.agent_loop_driver.llm_manager.resolve_model", resolve_model),
            patch("app.services.stream.agent_loop_driver.pick_fallback_model", MagicMock(return_value=fallback)),
        ):
            await run_agent_loop(
                db=object(),
                messages=[{"role": "user", "content": "南山区明天天气如何？"}],
                state=state,
                runtime=_runtime(
                    model_id="mimo-v2.6-flash",
                    start_step_fn=start_step_fn,
                    complete_step_fn=AsyncMock(),
                    run_round_fn=run_round_fn,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                    warning_fn=warnings.append,
                ),
            )
        return state, tool_round_models, warnings

    @staticmethod
    def _clarification_round_result():
        return AgentRoundResult(
            reasoning_buf="",
            content_buf="你说的南山区是深圳南山区吗？确认后我再查天气。",
            tool_calls=[],
            finish_reason="stop",
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
        )

    @staticmethod
    def _weather_round_result():
        return AgentRoundResult(
            reasoning_buf="",
            content_buf="",
            tool_calls=[{"id": "tc-weather", "name": "weather_forecast", "arguments": '{"location":"南山区"}'}],
            finish_reason="tool_calls",
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
        )

    async def test_first_output_timeout_switches_rest_of_answer_to_fallback_model(self):
        calls: list[tuple[str, str, float | None]] = []

        async def run_round_fn(**kwargs):
            calls.append((kwargs["model_id"], kwargs["litellm_model"], kwargs.get("first_output_timeout_s")))
            if len(calls) == 1:
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            if len(calls) == 2:
                return self._weather_round_result()
            return self._clarification_round_result()

        state, tool_round_models, warnings = await self._run_weather_tool_loop(
            run_round_fn,
            resolve_model=MagicMock(return_value=("litellm_proxy/deepseek-chat", "deepseek", {"api_base": "x"})),
        )

        self.assertEqual(
            calls,
            [
                ("mimo-v2.6-flash", "openai/gpt-4", FIRST_OUTPUT_TIMEOUT_S),
                ("deepseek-chat", "litellm_proxy/deepseek-chat", FALLBACK_FIRST_OUTPUT_TIMEOUT_S),
                ("deepseek-chat", "litellm_proxy/deepseek-chat", FALLBACK_FIRST_OUTPUT_TIMEOUT_S),
            ],
        )
        self.assertEqual(state.active_model_id, "deepseek-chat")
        self.assertEqual(tool_round_models, ["deepseek-chat"])
        self.assertTrue(any("改用备用模型" in warning for warning in warnings))

    async def test_first_output_timeout_retries_original_model_with_timeout_when_fallback_unavailable(self):
        calls: list[tuple[str, float | None]] = []

        async def run_round_fn(**kwargs):
            calls.append((kwargs["model_id"], kwargs.get("first_output_timeout_s")))
            if len(calls) == 1:
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            if len(calls) == 2:
                return self._weather_round_result()
            return self._clarification_round_result()

        state, tool_round_models, _warnings = await self._run_weather_tool_loop(
            run_round_fn,
            resolve_model=MagicMock(side_effect=ValueError("missing")),
        )

        # 没换成模型时原模型每轮仍限首字时长，不会无限等一个可能已经故障的服务商。
        self.assertEqual(calls, [("mimo-v2.6-flash", FIRST_OUTPUT_TIMEOUT_S)] * 3)
        self.assertIsNone(state.active_model_id)
        self.assertEqual(tool_round_models, ["mimo-v2.6-flash"])

    async def test_fallback_rounds_get_a_looser_first_output_timeout(self):
        captured = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs)
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            )

        switched = AgentLoopState()
        switched.active_model_id = "deepseek-chat"
        for state in (AgentLoopState(), switched):
            await _run_round(
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(model_id="deepseek-chat", run_round_fn=run_round_fn),
                step_number=1,
                step_context=AgentStepContext(
                    step_id="s", step_number=1, started_at=1.0, thinking_block_id="t", text_block_id="x"
                ),
            )

        # 用户自己选的 deepseek-chat 也按原模型限时（超时可以换别家）；换到备用模型后放宽，但不会不限时。
        self.assertEqual(captured[0]["first_output_timeout_s"], FIRST_OUTPUT_TIMEOUT_S)
        self.assertEqual(captured[1]["first_output_timeout_s"], FALLBACK_FIRST_OUTPUT_TIMEOUT_S)

    async def test_product_result_allows_sequential_multi_intent_tools_and_streams_model_answer(self):
        state = AgentLoopState()
        started_steps: list[int] = []
        llm_steps: list[int] = []
        defer_output_flags: list[bool] = []
        tool_calls = [
            {"id": "tc-cafe", "name": "local_place_search", "arguments": '{"query":"咖啡店"}'},
            {"id": "tc-pool", "name": "local_place_search", "arguments": '{"query":"桌球"}'},
        ]

        async def start_step_fn(**kwargs):
            started_steps.append(kwargs["step_number"])
            context = AgentStepContext(
                step_id=f"step-{kwargs['step_number']}",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{kwargs['step_number']}",
                text_block_id=f"text-{kwargs['step_number']}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            llm_steps.append(kwargs["step_number"])
            defer_output_flags.append(bool(kwargs.get("defer_output")))
            step_number = kwargs["step_number"]
            if step_number <= 2:
                return AgentRoundResult(
                    reasoning_buf="先查咖啡店，再查附近桌球" if step_number == 1 else "继续完成桌球意图",
                    content_buf="",
                    tool_calls=[tool_calls[step_number - 1]],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=step_number * 2, output_tokens=step_number * 3),
                    output_deferred=bool(kwargs.get("defer_output")),
                )
            return AgentRoundResult(
                reasoning_buf="模型可能生成未验证组合距离",
                content_buf="| 起点 | 终点 |\n| --- | --- |\n| 示例咖啡 | 示例桌球馆 |",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=7, output_tokens=9),
                output_deferred=bool(kwargs.get("defer_output")),
            )

        async def handle_tool_calls_round_fn(**kwargs):
            request = kwargs["request"]
            request.on_tools_executed(1)
            tool_call = request.tool_calls[0]
            query = "咖啡店" if tool_call["id"] == "tc-cafe" else "桌球"
            name = "示例咖啡" if tool_call["id"] == "tc-cafe" else "示例桌球馆"
            request.content_blocks.append(
                PlaceResultsBlock(
                    type="place_results",
                    schema_version=1,
                    provider="amap",
                    query=query,
                    status="success",
                    result_count=1,
                    places=[PlaceResult(name=name)],
                )
            )
            return ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["local_place_search"],
            )

        append_chunk = AsyncMock()
        with patch("app.services.stream_state_service.append_chunk", append_chunk):
            outcome = await run_agent_loop(
                db=object(),
                messages=[{"role": "user", "content": "附近咖啡"}],
                state=state,
                runtime=_runtime(
                    start_step_fn=start_step_fn,
                    complete_step_fn=AsyncMock(),
                    run_round_fn=run_round_fn,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                ),
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(llm_steps, [1, 2, 3])
        self.assertEqual(started_steps, [1, 2, 3])
        self.assertEqual(defer_output_flags, [False, False, False])
        # 回答已在本轮流式推送，收尾不再由服务端补发正文。
        append_chunk.assert_not_awaited()
        self.assertIn("示例桌球馆", state.content_blocks[-1].text)
        self.assertEqual(state.total_tool_calls, 2)
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=7, output_tokens=9))
        self.assertEqual(
            [block.type for block in state.content_blocks],
            ["place_results", "place_results", "thinking", "text"],
        )

    async def test_existing_product_result_streams_next_round_model_output(self):
        state = AgentLoopState(
            content_blocks=[
                PlaceResultsBlock(
                    type="place_results",
                    schema_version=1,
                    provider="amap",
                    query="咖啡",
                    status="success",
                    result_count=1,
                    places=[PlaceResult(name="示例咖啡")],
                )
            ]
        )

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id="step-product-final",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id="thinking-product-final",
                text_block_id="text-product-final",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            self.assertFalse(kwargs.get("defer_output", False))
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="最终回答",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=2, output_tokens=3),
            )

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "附近咖啡"}],
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                complete_step_fn=AsyncMock(),
                run_round_fn=run_round_fn,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)

    async def test_stop_round_completes_text_step_and_returns_completed(self):
        state = AgentLoopState()
        started_steps: list[int] = []
        completed_steps: list[str] = []

        async def start_step_fn(**kwargs):
            started_steps.append(kwargs["step_number"])
            context = AgentStepContext(
                step_id="step-1",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id="thinking-1",
                text_block_id="text-1",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)
            return 25

        async def run_round_fn(**kwargs):
            self.assertEqual(kwargs["step_number"], 1)
            return AgentRoundResult(
                reasoning_buf="思考",
                content_buf="回答",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=3, output_tokens=5),
            )

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                complete_step_fn=complete_step_fn,
                run_round_fn=run_round_fn,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(started_steps, [1])
        self.assertEqual(completed_steps, ["step-1"])
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(state.finish_reason, "stop")
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=3, output_tokens=5))
        self.assertEqual([block.type for block in state.content_blocks], ["thinking", "text"])
        self.assertEqual(state.content_blocks[0].thinking, "思考")
        self.assertEqual(state.content_blocks[1].text, "回答")

    async def test_output_length_round_delivers_truncated_text_as_limit_reached_without_summary(self):
        # 触达 max_tokens 曾被当成正常 stop：截断的代码按已完成交付，用户无从得知。
        state = AgentLoopState()
        emitter = DummyEmitter(limit_reasons=[])

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id="step-1",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id="thinking-1",
                text_block_id="text-1",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**_kwargs):
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="```html\n<div>未写完",
                tool_calls=[],
                finish_reason="length",
                accumulated_usage=Usage(input_tokens=3, output_tokens=5),
            )

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "写个游戏"}],
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                complete_step_fn=_complete_step_noop,
                run_round_fn=run_round_fn,
                emitter=emitter,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(emitter.limit_reasons, ["output_length"])
        self.assertEqual(state.limit_reason, "output_length")
        self.assertEqual(state.content_blocks[-1].text, "```html\n<div>未写完")
        self.assertEqual(
            map_run_terminal_state(unknown_terminated=state.unknown_terminated, limit_reason=state.limit_reason),
            AgentRunTerminalState(run_finish_reason="limit_reached", session_status="limit_reached"),
        )

    async def test_immediate_limit_runs_summary_step_and_returns_completed(self):
        state = AgentLoopState()
        state.step = 8
        emitter = DummyEmitter(limit_reasons=[])
        summary_calls = []

        async def run_limit_summary_step_fn(**kwargs):
            summary_calls.append(kwargs)
            request = kwargs["request"]
            request.content_blocks.append(TextBlock(type="text", id="summary-text", text="总结回答"))
            request.on_step_started("summary-step")
            await request.complete_step_fn(context=SimpleNamespace(step_id="summary-step"))
            return LimitSummaryOutcome(accumulated_usage=Usage(input_tokens=11, output_tokens=13))

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            runtime=_runtime(
                complete_step_fn=_complete_step_noop,
                emitter=emitter,
                run_limit_summary_step_fn=run_limit_summary_step_fn,
                clock=lambda: 1.0,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(emitter.limit_reasons, ["max_steps"])
        self.assertEqual(state.limit_reason, "max_steps")
        self.assertEqual(state.finish_reason, "tool_calls")
        self.assertEqual(state.step, 9)
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=11, output_tokens=13))
        self.assertEqual(state.content_blocks[-1].text, "总结回答")
        self.assertEqual(summary_calls[0]["request"].step_number, 9)
        self.assertEqual(summary_calls[0]["request"].messages[0], {"role": "user", "content": "hi"})

    async def test_immediate_timeout_marks_timeout_and_runs_summary_step(self):
        state = AgentLoopState()
        emitter = DummyEmitter(limit_reasons=[])
        summary_calls = []

        async def run_limit_summary_step_fn(**kwargs):
            summary_calls.append(kwargs)
            kwargs["request"].on_step_started("summary-timeout-step")
            await kwargs["request"].complete_step_fn(context=SimpleNamespace(step_id="summary-timeout-step"))
            return LimitSummaryOutcome(accumulated_usage=Usage(input_tokens=1, output_tokens=1))

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            runtime=_runtime(
                complete_step_fn=_complete_step_noop,
                emitter=emitter,
                limits=AgentLoopLimits(max_steps=8, max_tool_calls=20, total_timeout_s=30),
                run_limit_summary_step_fn=run_limit_summary_step_fn,
                clock=lambda: 31.0,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(emitter.limit_reasons, ["timeout"])
        self.assertEqual(state.limit_reason, "timeout")
        self.assertEqual(state.finish_reason, "timeout")
        self.assertEqual(state.step, 1)
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(summary_calls[0]["request"].step_number, 1)

    async def test_tool_calls_round_delegates_and_continues_to_stop(self):
        state = AgentLoopState()
        messages = [{"role": "user", "content": "hi"}]
        tool_call = {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'}
        started_steps: list[int] = []
        tool_round_calls = []
        completed_steps: list[str] = []
        call_kwargs = {}
        emitter = DummyEmitter(limit_reasons=[])
        session_cache = object()
        network_budget = object()

        def clock():
            return 1.0

        async def start_step_fn(**kwargs):
            step_number = kwargs["step_number"]
            started_steps.append(step_number)
            context = AgentStepContext(
                step_id=f"step-{step_number}",
                step_number=step_number,
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{step_number}",
                text_block_id=f"text-{step_number}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            if kwargs["step_number"] == 1:
                self.assertEqual(kwargs["accumulated_usage"], Usage(input_tokens=0, output_tokens=0))
                return AgentRoundResult(
                    reasoning_buf="需要搜索",
                    content_buf="",
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                )
            self.assertEqual(kwargs["step_number"], 2)
            self.assertEqual(kwargs["accumulated_usage"], Usage(input_tokens=2, output_tokens=3))
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="最终回答",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=5, output_tokens=8),
            )

        async def handle_tool_calls_round_fn(**kwargs):
            request = kwargs["request"]
            tool_round_calls.append(request)
            request.on_tools_executed(len(request.tool_calls))
            request.messages.append({"role": "tool", "tool_call_id": "tc-1", "content": "搜索结果"})

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)
            return 25

        runtime = _runtime(
            start_step_fn=start_step_fn,
            complete_step_fn=complete_step_fn,
            run_round_fn=run_round_fn,
            handle_tool_calls_round_fn=handle_tool_calls_round_fn,
            call_kwargs=call_kwargs,
            emitter=emitter,
            session_cache=session_cache,
            network_budget=network_budget,
            clock=clock,
        )
        outcome = await run_agent_loop(
            db="db",
            messages=messages,
            state=state,
            runtime=runtime,
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(started_steps, [1, 2])
        self.assertEqual(len(tool_round_calls), 1)
        self.assertEqual(tool_round_calls[0].db, "db")
        self.assertEqual(tool_round_calls[0].step_number, 1)
        self.assertEqual(tool_round_calls[0].tool_calls, [tool_call])
        self.assertEqual(tool_round_calls[0].reasoning_buf, "需要搜索")
        self.assertIs(tool_round_calls[0].messages, messages)
        self.assertIs(tool_round_calls[0].content_blocks, state.content_blocks)
        self.assertIs(tool_round_calls[0].call_kwargs, call_kwargs)
        self.assertIs(tool_round_calls[0].emitter, emitter)
        self.assertIs(tool_round_calls[0].session_cache, session_cache)
        self.assertIs(tool_round_calls[0].network_budget, network_budget)
        self.assertIs(tool_round_calls[0].clock, clock)
        self.assertEqual(completed_steps, ["step-2"])
        self.assertEqual(state.total_tool_calls, 1)
        self.assertEqual(state.step, 2)
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=5, output_tokens=8))
        self.assertEqual([block.type for block in state.content_blocks], ["text"])
        self.assertEqual(state.content_blocks[0].text, "最终回答")
        self.assertEqual(messages[-1], {"role": "tool", "tool_call_id": "tc-1", "content": "搜索结果"})

    async def test_empty_stop_after_tools_runs_summary_step(self):
        state = AgentLoopState()
        messages = [{"role": "user", "content": "hi"}]
        tool_call = {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'}
        started_steps: list[int] = []
        completed_steps: list[str] = []
        summary_calls = []

        async def start_step_fn(**kwargs):
            step_number = kwargs["step_number"]
            started_steps.append(step_number)
            context = AgentStepContext(
                step_id=f"step-{step_number}",
                step_number=step_number,
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{step_number}",
                text_block_id=f"text-{step_number}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**kwargs):
            if kwargs["step_number"] == 1:
                return AgentRoundResult(
                    reasoning_buf="需要搜索",
                    content_buf="",
                    tool_calls=[tool_call],
                    finish_reason="tool_calls",
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                )
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=5, output_tokens=8),
            )

        async def handle_tool_calls_round_fn(**kwargs):
            request = kwargs["request"]
            request.on_tools_executed(len(request.tool_calls))
            request.messages.append({"role": "tool", "tool_call_id": "tc-1", "content": "搜索结果"})

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)

        async def run_limit_summary_step_fn(**kwargs):
            summary_calls.append(kwargs["request"])
            request = kwargs["request"]
            request.content_blocks.append(TextBlock(type="text", id="summary-text", text="总结回答"))
            request.on_step_started("summary-step")
            await request.complete_step_fn(context=SimpleNamespace(step_id="summary-step"))
            return LimitSummaryOutcome(accumulated_usage=Usage(input_tokens=9, output_tokens=13))

        outcome = await run_agent_loop(
            db="db",
            messages=messages,
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                complete_step_fn=complete_step_fn,
                run_round_fn=run_round_fn,
                handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                run_limit_summary_step_fn=run_limit_summary_step_fn,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(started_steps, [1, 2])
        self.assertEqual(completed_steps, ["step-2", "summary-step"])
        self.assertEqual(len(summary_calls), 1)
        self.assertEqual(summary_calls[0].step_number, 3)
        self.assertEqual(state.finish_reason, "empty_answer_summary")
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=9, output_tokens=13))
        self.assertEqual([block.type for block in state.content_blocks], ["text"])
        self.assertEqual(state.content_blocks[0].text, "总结回答")
        self.assertEqual(state.current_step_id, None)

    async def test_unknown_tool_calls_without_tool_list_marks_incomplete_and_completes_step(self):
        state = AgentLoopState()
        completed_steps: list[str] = []

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id="step-unknown",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id="thinking-unknown",
                text_block_id="text-unknown",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**_kwargs):
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="退化回答",
                tool_calls=[],
                finish_reason="tool_calls",
                accumulated_usage=Usage(input_tokens=7, output_tokens=9),
            )

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)
            return 25

        outcome = await run_agent_loop(
            db=object(),
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                complete_step_fn=complete_step_fn,
                run_round_fn=run_round_fn,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(completed_steps, ["step-unknown"])
        self.assertEqual(state.finish_reason, "tool_calls")
        self.assertTrue(state.unknown_terminated)
        self.assertEqual(state.current_step_id, None)
        self.assertEqual(state.accumulated_usage, Usage(input_tokens=7, output_tokens=9))
        self.assertEqual([block.type for block in state.content_blocks], ["text"])
        self.assertEqual(state.content_blocks[0].text, "退化回答")

    async def test_cancelled_round_returns_superseded_without_terminal_side_effects(self):
        state = AgentLoopState()
        persist_calls = []

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id="step-cancelled",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id="thinking-cancelled",
                text_block_id="text-cancelled",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def run_round_fn(**_kwargs):
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="半截回答",
                tool_calls=[],
                finish_reason="cancelled",
                accumulated_usage=Usage(input_tokens=2, output_tokens=1),
            )

        def persist_message_fn(db, message_id, conversation_id, model_id, content_blocks, usage_data=None):
            persist_calls.append(
                {
                    "db": db,
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "model_id": model_id,
                    "block_types": [block.type for block in content_blocks],
                    "usage_data": usage_data,
                }
            )

        outcome = await run_agent_loop(
            db="db",
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            runtime=_runtime(
                start_step_fn=start_step_fn,
                run_round_fn=run_round_fn,
                persist_message_fn=persist_message_fn,
            ),
        )

        self.assertEqual(outcome.exit, AgentLoopExit.SUPERSEDED)
        self.assertEqual(outcome.error_msg, "被新请求取代")
        self.assertEqual([block.type for block in state.content_blocks], ["text"])
        self.assertEqual(state.content_blocks[0].text, "半截回答")
        self.assertEqual(state.final_usage(), Usage(input_tokens=2, output_tokens=1))
        self.assertEqual(state.current_step_id, "step-cancelled")
        self.assertEqual(persist_calls, [])
        self.assertFalse(state.terminal_emitted)

    async def test_cancel_between_steps_does_not_leave_completed_step_as_current(self):
        """step 完成即释放 current_step_id；下一步开始前被取消时，终态收尾不会误标已完成的 step。"""
        state = AgentLoopState()
        completed_steps: list[str] = []

        async def start_step_fn(**kwargs):
            if kwargs["step_number"] > 1:
                raise asyncio.CancelledError
            context = AgentStepContext(
                step_id="step-1",
                step_number=1,
                started_at=kwargs["clock"](),
                thinking_block_id="step-1-thinking",
                text_block_id="step-1-text",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def complete_step_fn(**kwargs):
            completed_steps.append(kwargs["context"].step_id)

        async def run_round_fn(**_kwargs):
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[{"id": "call-1", "name": "web_search", "arguments": "{}"}],
                finish_reason="tool_calls",
                accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            )

        async def handle_tool_calls_round_fn(*, request):
            await request.complete_step_fn(context=request.step_context)
            return ToolRoundOutcome(tool_call_count=1, tool_names=["web_search"])

        with self.assertRaises(asyncio.CancelledError):
            await run_agent_loop(
                db="db",
                messages=[{"role": "user", "content": "hi"}],
                state=state,
                runtime=_runtime(
                    start_step_fn=start_step_fn,
                    complete_step_fn=complete_step_fn,
                    run_round_fn=run_round_fn,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                ),
            )

        self.assertEqual(completed_steps, ["step-1"])
        self.assertIsNone(state.current_step_id)


if __name__ == "__main__":
    unittest.main()


class OrdinaryRunWorksetContextTests(unittest.IsolatedAsyncioTestCase):
    """终结总结要看到实际读取状态。

    #127 的三个确证样本都是同一形态：模型声称读了从未发起读页的 URL。此前普通请求
    的上下文里只注入来源正文，没有任何一处逐条说明「本轮哪些读成功、哪些失败」，
    模型只能从散落各轮的工具结果自己拼，拼不出来就填一段合理的叙述。
    """

    def _state_with_one_read(self) -> AgentLoopState:
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-ordinary", mode="off"))
        state.record_research_content_blocks(
            [
                UrlBlock(
                    type="url_read",
                    url="https://example.test/read-ok",
                    title="已读到的页面",
                    source_refs=[
                        SourceReference(
                            kind="url_read",
                            title="已读到的页面",
                            url="https://example.test/read-ok",
                            evidence_id="ev-ok",
                            citation_index=1,
                        )
                    ],
                    source_count=1,
                )
            ],
            summaries={"ev-ok": ("正文片段", ["正文片段"])},
        )
        return state

    async def test_terminal_summary_injects_actual_read_status_for_ordinary_run(self):
        state = self._state_with_one_read()
        messages = messages_with_research_workset(
            [{"role": "user", "content": "请分别打开这两个链接"}],
            workset=state.research_workset,
        )

        system_contents = [message.content for message in messages if message.role == "system"]
        self.assertTrue(
            any("status=read_success" in content for content in system_contents),
            msg="普通请求的终结总结必须逐条给出实际读取状态",
        )
        self.assertEqual(
            [message.section_id for message in messages].count(RESEARCH_EVIDENCE_WORKSET),
            1,
            msg="读取状态清单只应注入一次",
        )

    async def test_empty_workset_does_not_add_sections(self):
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-empty", mode="off"))
        messages = messages_with_research_workset(
            [{"role": "user", "content": "你好"}],
            workset=state.research_workset,
        )

        self.assertNotIn(RESEARCH_EVIDENCE_WORKSET, [message.section_id for message in messages])

    async def test_terminal_summary_reports_requested_urls_that_were_never_fetched(self):
        """只读了一个、用户给了两个时，上下文必须说明有目标未发起读取。

        #127 三个样本的共同点：某个目标从未发起 url_read，因此不会进入 workset。
        只列「读到了什么」仍是一份不完整的事实，模型照样可以声称两页都读到。
        """
        state = self._state_with_one_read()
        messages = messages_with_research_workset(
            [
                {
                    "role": "user",
                    "content": "请分别打开 https://example.test/read-ok 和 https://example.test/never-tried",
                }
            ],
            workset=state.research_workset,
        )

        system_contents = [message.content for message in messages if message.role == "system"]
        self.assertTrue(
            any("never fetched" in content for content in system_contents),
            msg="未发起读取的目标数量必须出现在上下文里",
        )
        self.assertTrue(any("1 URL(s)" in content for content in system_contents))
        self.assertFalse(
            any("never-tried" in content for content in system_contents),
            msg="用户原文属外部输入，不得回显进 system",
        )

    async def test_zero_read_request_still_reports_unattempted_targets(self):
        """一次读页都没发起时也要说明——这正是 #127 首个样本的形态。"""
        state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-none", mode="off"))
        messages = messages_with_research_workset(
            [{"role": "user", "content": "请打开 https://example.test/a 和 https://example.test/b"}],
            workset=state.research_workset,
        )

        system_contents = [message.content for message in messages if message.role == "system"]
        self.assertTrue(any("2 URL(s)" in content for content in system_contents))

    async def test_all_requested_urls_read_adds_no_unattempted_notice(self):
        state = self._state_with_one_read()
        messages = messages_with_research_workset(
            [{"role": "user", "content": "请打开 https://example.test/read-ok"}],
            workset=state.research_workset,
        )

        system_contents = [message.content for message in messages if message.role == "system"]
        self.assertFalse(any("never fetched" in content for content in system_contents))


def _sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0


class ModelUnavailableFallbackTests(unittest.IsolatedAsyncioTestCase):
    """原模型本轮不可用时换备用模型；备用模型能力不够时不换；连续失败打开熔断。"""

    FALLBACK_CAPABILITIES = {"functionCalling": True, "vision": False, "thinkingSwitchable": True}

    def setUp(self):
        self.pick_fallback = MagicMock(return_value="deepseek-chat")

    async def _run_loop(
        self, run_round_fn, *, messages=None, call_kwargs=None, should_use_reasoning=True, allowed_model_ids=None
    ):
        state = AgentLoopState()
        warnings: list[str] = []

        async def start_step_fn(**kwargs):
            context = AgentStepContext(
                step_id=f"step-{kwargs['step_number']}",
                step_number=kwargs["step_number"],
                started_at=kwargs["clock"](),
                thinking_block_id=f"thinking-{kwargs['step_number']}",
                text_block_id=f"text-{kwargs['step_number']}",
            )
            kwargs["on_step_started"](context.step_id)
            return context

        async def handle_tool_calls_round_fn(**kwargs):
            kwargs["request"].on_tools_executed(1)
            return ToolRoundOutcome(tool_call_count=1, tool_names=["weather_forecast"])

        with (
            patch("app.services.stream_state_service.append_chunk", AsyncMock()),
            patch(
                "app.services.stream.agent_loop_driver.llm_manager.resolve_model",
                MagicMock(return_value=("litellm_proxy/deepseek-chat", "deepseek", {"api_base": "x"})),
            ),
            patch("app.services.stream.agent_loop_driver.pick_fallback_model", self.pick_fallback),
            patch(
                "app.services.stream.agent_loop_driver.litellm_catalog.get_capabilities",
                MagicMock(return_value=self.FALLBACK_CAPABILITIES),
            ),
        ):
            await run_agent_loop(
                db=object(),
                messages=messages or [{"role": "user", "content": "南山区明天天气如何？"}],
                state=state,
                runtime=_runtime(
                    model_id="gemini-4-argon",
                    provider="gemini",
                    litellm_model="litellm_proxy/gemini-4-argon",
                    should_use_reasoning=should_use_reasoning,
                    call_kwargs=call_kwargs or {},
                    start_step_fn=start_step_fn,
                    complete_step_fn=AsyncMock(),
                    run_round_fn=run_round_fn,
                    handle_tool_calls_round_fn=handle_tool_calls_round_fn,
                    warning_fn=warnings.append,
                    allowed_model_ids=allowed_model_ids,
                ),
            )
        return state, warnings

    async def test_server_error_after_tool_round_switches_to_fallback_instead_of_failing(self):
        from app.services.stream.first_output_timeout import ModelRoundUnavailableError

        calls: list[str] = []

        async def run_round_fn(**kwargs):
            calls.append(kwargs["model_id"])
            if len(calls) == 1:
                return _Rounds.weather_round_result()
            if len(calls) == 2:
                raise ModelRoundUnavailableError(kwargs["model_id"], "http_500")
            return _Rounds.answer_round_result()

        state, warnings = await self._run_loop(run_round_fn)

        self.assertEqual(calls, ["gemini-4-argon", "gemini-4-argon", "deepseek-chat"])
        self.assertEqual(state.active_model_id, "deepseek-chat")
        self.assertEqual(state.total_tool_calls, 1)
        self.assertTrue(any("reason=http_500" in warning for warning in warnings))

    async def test_fallback_failure_still_fails_the_run(self):
        from app.services.stream.first_output_timeout import ModelRoundUnavailableError

        calls: list[str] = []

        async def run_round_fn(**kwargs):
            calls.append(kwargs["model_id"])
            raise ModelRoundUnavailableError(kwargs["model_id"], "http_503")

        with self.assertRaises(ModelRoundUnavailableError):
            await self._run_loop(run_round_fn)

        self.assertEqual(calls, ["gemini-4-argon", "deepseek-chat"])

    async def test_image_request_without_capable_fallback_retries_original_once_with_timeout(self):
        calls: list[tuple[str, float | None]] = []
        self.pick_fallback.return_value = None

        async def run_round_fn(**kwargs):
            calls.append((kwargs["model_id"], kwargs.get("first_output_timeout_s")))
            raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])

        image_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "图里是什么？"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
        fallback_before = _sample(
            "fusion_model_fallback_total", model="gemini-4-argon", fallback="none", reason="first_output_timeout"
        )
        unavailable_before = _sample(
            "fusion_model_round_unavailable_total", model="gemini-4-argon", reason="first_output_timeout"
        )
        # 服务商卡住时：原模型限时再试一次后失败，不无限等。
        with self.assertRaises(FirstOutputTimeoutError):
            await self._run_loop(
                run_round_fn,
                messages=[image_message],
                call_kwargs={"tools": [_tool_definition("web_search")]},
                allowed_model_ids=("mimo-v2.6-flash", "deepseek-chat"),
            )

        self.assertEqual(calls, [("gemini-4-argon", FIRST_OUTPUT_TIMEOUT_S)] * 2)
        self.assertEqual(
            _sample(
                "fusion_model_fallback_total",
                model="gemini-4-argon",
                fallback="none",
                reason="first_output_timeout",
            ),
            fallback_before + 1,
        )
        self.assertEqual(
            _sample("fusion_model_round_unavailable_total", model="gemini-4-argon", reason="first_output_timeout"),
            unavailable_before + 2,
        )
        pick_kwargs = self.pick_fallback.call_args.kwargs
        self.assertEqual(pick_kwargs["failed_model_id"], "gemini-4-argon")
        self.assertEqual(pick_kwargs["required_capabilities"], ("agentTools", "vision"))
        self.assertEqual(pick_kwargs["allowed_model_ids"], ("mimo-v2.6-flash", "deepseek-chat"))

    async def test_picked_fallback_model_is_used_for_rest_of_answer(self):
        calls: list[str] = []
        self.pick_fallback.return_value = "qwen3.8-flash"

        async def run_round_fn(**kwargs):
            calls.append(kwargs["model_id"])
            if len(calls) == 1:
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            if len(calls) == 2:
                return _Rounds.weather_round_result()
            return _Rounds.answer_round_result()

        labels = {"model": "gemini-4-argon", "fallback": "qwen3.8-flash", "reason": "first_output_timeout"}
        before = _sample("fusion_model_fallback_total", **labels)
        state, warnings = await self._run_loop(run_round_fn)

        self.assertEqual(_sample("fusion_model_fallback_total", **labels), before + 1)
        self.assertEqual(calls, ["gemini-4-argon", "qwen3.8-flash", "qwen3.8-flash"])
        self.assertEqual(state.active_model_id, "qwen3.8-flash")
        self.assertTrue(any("fallback=qwen3.8-flash" in warning for warning in warnings))
        self.assertEqual(self.pick_fallback.call_args.kwargs["required_capabilities"], ())

    async def test_switch_rebuilds_reasoning_params_for_fallback_provider(self):
        captured: list[dict] = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs["call_kwargs"])
            if len(captured) == 1:
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            return _Rounds.answer_round_result()

        await self._run_loop(
            run_round_fn,
            # 准备阶段按 Gemini 写入的关思考参数
            call_kwargs={"tools": [_tool_definition("web_search")], "tool_choice": "auto", "reasoning_effort": "none"},
            should_use_reasoning=False,
        )

        fallback_kwargs = captured[1]
        self.assertNotIn("reasoning_effort", fallback_kwargs)
        self.assertEqual(fallback_kwargs["extra_body"], {"extra_body": {"thinking": {"type": "disabled"}}})
        self.assertEqual(fallback_kwargs["tool_choice"], "auto")

    async def test_switch_clamps_output_cap_to_fallback_model_limits(self):
        captured: list[dict] = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs["call_kwargs"])
            if len(captured) == 1:
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            return _Rounds.answer_round_result()

        with patch(
            "app.services.stream.agent_loop_request_prep.litellm_catalog.get_model_entry",
            MagicMock(return_value={"max_input_tokens": 1_000_000, "max_output_tokens": 8192}),
        ):
            await self._run_loop(run_round_fn, call_kwargs={"max_tokens": 65536})

        self.assertEqual(captured[0]["max_tokens"], 65536)
        self.assertEqual(captured[1]["max_tokens"], 8192)

    async def test_open_circuit_goes_straight_to_fallback_and_success_resets_it(self):
        from app.ai.model_circuit_breaker import model_circuit_breaker

        model_circuit_breaker.record_failure("gemini-4-argon", "first_output_timeout")
        model_circuit_breaker.record_failure("gemini-4-argon", "first_output_timeout")
        calls: list[tuple[str, float | None]] = []

        async def run_round_fn(**kwargs):
            calls.append((kwargs["model_id"], kwargs.get("first_output_timeout_s")))
            return _Rounds.answer_round_result()

        state, warnings = await self._run_loop(run_round_fn)

        self.assertEqual(calls, [("deepseek-chat", FALLBACK_FIRST_OUTPUT_TIMEOUT_S)])
        self.assertEqual(state.active_model_id, "deepseek-chat")
        self.assertTrue(any("reason=circuit_open" in warning for warning in warnings))
        self.assertTrue(model_circuit_breaker.is_open("gemini-4-argon"))
        self.assertFalse(model_circuit_breaker.is_open("deepseek-chat"))

    async def test_open_circuit_without_fallback_still_limits_original_model(self):
        from app.ai.model_circuit_breaker import model_circuit_breaker

        for _ in range(model_circuit_breaker.failure_threshold):
            model_circuit_breaker.record_failure("gemini-4-argon", "http_500")
        self.pick_fallback.return_value = None
        calls: list[tuple[str, float | None]] = []

        async def run_round_fn(**kwargs):
            calls.append((kwargs["model_id"], kwargs.get("first_output_timeout_s")))
            return _Rounds.answer_round_result()

        state, _warnings = await self._run_loop(run_round_fn)

        self.assertEqual(calls, [("gemini-4-argon", FIRST_OUTPUT_TIMEOUT_S)])
        self.assertIsNone(state.active_model_id)
        self.assertFalse(model_circuit_breaker.is_open("gemini-4-argon"))

    async def test_consecutive_timeouts_across_runs_open_the_circuit(self):
        from app.ai.model_circuit_breaker import model_circuit_breaker

        async def run_round_fn(**kwargs):
            if kwargs["model_id"] == "gemini-4-argon":
                raise FirstOutputTimeoutError(kwargs["model_id"], kwargs["first_output_timeout_s"])
            return _Rounds.answer_round_result()

        await self._run_loop(run_round_fn)
        self.assertFalse(model_circuit_breaker.is_open("gemini-4-argon"))
        await self._run_loop(run_round_fn)
        self.assertTrue(model_circuit_breaker.is_open("gemini-4-argon"))


class _Rounds:
    @staticmethod
    def weather_round_result():
        return AgentRoundResult(
            reasoning_buf="",
            content_buf="",
            tool_calls=[{"id": "tc-weather", "name": "weather_forecast", "arguments": '{"location":"南山区"}'}],
            finish_reason="tool_calls",
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
        )

    @staticmethod
    def answer_round_result():
        return AgentRoundResult(
            reasoning_buf="",
            content_buf="深圳南山区明天多云，22 到 27 度。",
            tool_calls=[],
            finish_reason="stop",
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
        )
