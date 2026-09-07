"""预算触顶时通过实际正文提交路径交付已有事实和未完成计划。"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.prompts.section_ids import PLAN_EXECUTION_REPAIR
from app.schemas.chat import PlaceResult, PlaceResultsBlock
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import AgentLoopExit, run_agent_loop
from app.services.stream.agent_loop_policy import AgentLoopLimits, map_run_terminal_state
from app.services.stream.agent_loop_run_completion import AgentLoopRunCompletionContext, finalize_completed_run
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.step_lifecycle import AgentStepContext
from test.services.stream.test_agent_loop_driver import _runtime


def _coordinator(unfinished_plan):
    coordinator = PlanCoordinator(run_id="run", mode="on" if unfinished_plan else "off")
    if unfinished_plan:
        update = coordinator.apply_model_update(
            {
                "reason": "先查询再整理",
                "items": [
                    {
                        "id": "places",
                        "title": "查咖啡店",
                        "status": "pending",
                        "kind": "search",
                        "depends_on": [],
                        "planned_tools": ["local_place_search"],
                    },
                    {
                        "id": "weather",
                        "title": "查天气",
                        "status": "pending",
                        "kind": "search",
                        "depends_on": ["places"],
                        "planned_tools": ["weather_forecast"],
                    },
                    {
                        "id": "answer",
                        "title": "整理",
                        "status": "pending",
                        "kind": "answer",
                        "depends_on": ["weather"],
                        "planned_tools": [],
                    },
                ],
            }
        )
        assert update.accepted
        coordinator.mark_tool_results({"places": "completed"})
    return coordinator


def _state(limit, unfinished_plan):
    return AgentLoopState(
        plan_coordinator=_coordinator(unfinished_plan),
        step=8 if limit == "max_steps" else 1,
        total_tool_calls=20 if limit == "max_tool_calls" else 1,
        product_tool_attempted=True,
        content_blocks=[
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="咖啡店",
                status="success",
                result_count=1,
                places=[PlaceResult(name="真实咖啡店")],
            )
        ],
    )


async def _start_step(**kwargs):
    return AgentStepContext(
        step_id="terminal",
        step_number=kwargs["step_number"],
        started_at=kwargs["clock"](),
        thinking_block_id="thinking",
        text_block_id="answer",
    )


@pytest.mark.parametrize("limit", ["max_steps", "max_tool_calls", "timeout"])
@pytest.mark.parametrize("unfinished_plan", [False, True])
def test_limit_outputs_known_place_and_unfinished_plan_without_more_execution(limit, unfinished_plan):
    asyncio.run(_run_limit_case(limit, unfinished_plan))


async def _run_limit_case(limit, unfinished_plan):
    state = _state(limit, unfinished_plan)
    messages = [{"role": "user", "content": "查附近咖啡店，再查天气"}]
    runtime = _runtime(
        plan_mode="on" if unfinished_plan else "off",
        limits=AgentLoopLimits(max_steps=8, max_tool_calls=20, total_timeout_s=30),
        clock=lambda: 31.0 if limit == "timeout" else 1.0,
        start_step_fn=_start_step,
        complete_step_fn=AsyncMock(),
    )
    output = []

    async def collect_chunk(conversation_id, stage, text, block_id, **kwargs):
        assert stage == "answering"
        output.append(text)

    with patch("app.services.stream.agent_loop_round_outcome.append_chunk", collect_chunk):
        outcome = await run_agent_loop(db=object(), messages=messages, state=state, runtime=runtime)
    answer = "".join(output)
    assert "真实咖啡店" in answer
    if unfinished_plan:
        assert "天气" in answer and "未完成" in answer
        assert next(item for item in state.plan_coordinator.items if item["id"] == "weather")["status"] != "completed"
    assert not any(getattr(message, "section_id", None) == PLAN_EXECUTION_REPAIR for message in messages)
    assert outcome.exit == AgentLoopExit.COMPLETED
    assert state.limit_reason == limit
    assert any(getattr(block, "text", None) == answer for block in state.content_blocks)
    await _assert_terminal_state(state, runtime, limit, unfinished_plan)


async def _assert_terminal_state(state, runtime, limit, unfinished_plan):
    terminal = map_run_terminal_state(unknown_terminated=state.unknown_terminated, limit_reason=state.limit_reason)
    complete_run = AsyncMock()
    context = AgentLoopRunCompletionContext(
        db=object(),
        conversation_id="conv",
        task_id="task",
        run_id="run",
        model_id="model",
        provider="test",
        assistant_message_id="assistant",
        emitter=runtime.emitter,
        session_cache=runtime.session_cache,
        state=state,
        duration_ms_factory=lambda: 1,
    )
    await finalize_completed_run(
        context=context,
        terminal_state=terminal,
        persist_message_fn=lambda *_a, **_k: True,
        complete_agent_run_fn=complete_run,
        finalize_stream_fn=AsyncMock(),
    )
    assert complete_run.call_args.kwargs["finish_reason"] == "limit_reached"
    assert complete_run.call_args.kwargs["limit_reason"] == limit
    if unfinished_plan:
        assert {item["id"]: item["status"] for item in state.plan_coordinator.items} == {
            "places": "completed",
            "weather": "blocked",
            "answer": "completed",
        }
