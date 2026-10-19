from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from app.schemas.chat import (
    SelectedItineraryBlock,
    TrainOption,
    TrainResultsBlock,
    TravelEndpoint,
    TravelMoney,
)
from app.services.stream import tool_round as tool_round_module
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.product_structured_answers import PRODUCT_STRUCTURED_ANSWERS
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_context import ToolContextResolution
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import handle_tool_calls_round
from app.services.tool_handlers.base import ToolResult, ToolRuntimeContext
from app.services.tool_handlers.itinerary import PresentItineraryHandler

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _train_block() -> TrainResultsBlock:
    return TrainResultsBlock(
        type="train_results",
        id="blk-train-outbound",
        schema_version=1,
        provider="flyai",
        status="success",
        origin="深圳",
        destination="上海",
        departure_date="2026-08-01",
        observed_at=datetime(2026, 7, 26, 8, tzinfo=SHANGHAI_TZ),
        result_count=1,
        trains=[
            TrainOption(
                option_id="train-outbound-1",
                train_no="G100",
                train_type="高铁",
                departure=TravelEndpoint(
                    city="深圳",
                    station_name="深圳北站",
                    scheduled_at=datetime(2026, 8, 1, 8, tzinfo=SHANGHAI_TZ),
                ),
                arrival=TravelEndpoint(
                    city="上海",
                    station_name="上海虹桥站",
                    scheduled_at=datetime(2026, 8, 1, 16, tzinfo=SHANGHAI_TZ),
                ),
                duration_s=28_800,
                stops=0,
                price=TravelMoney(currency="CNY", amount_minor=56000),
            )
        ],
    )


def _record(
    *,
    tool_call: dict,
    status: str,
    block=None,
) -> ToolExecutionRecord:
    handler = Mock()
    handler.format_llm_context.return_value = "安全工具上下文"
    handler.build_content_block.return_value = block
    return ToolExecutionRecord(
        tool_call=tool_call,
        result=ToolResult(status=status),
        handler=handler,
        block_id=block.id if block is not None else f"blk-{tool_call['id']}",
        log_id=f"log-{tool_call['id']}",
    )


def _request(
    records: list[ToolExecutionRecord],
    *,
    state: AgentLoopState | None = None,
):
    state = state or AgentLoopState()
    tool_calls = [record.tool_call for record in records]
    order: list[tuple] = []
    emitter = AsyncMock()

    async def emit_content_block(*, tool_call_id, content_block):
        order.append(("emit", tool_call_id, content_block))

    emitter.content_block_upserted.side_effect = emit_content_block

    async def discard_content_block(*, block_id):
        order.append(("discard", block_id))

    emitter.content_block_discarded.side_effect = discard_content_block

    def persist_message_fn(db, msg_id, conv_id, model_id, blocks, usage_data=None, partial=False):
        order.append(("persist", partial, list(blocks)))

    async def execute_tools_fn(*args, **kwargs):
        order.append(("execute",))
        return records

    async def complete_step_fn(**kwargs):
        order.append(("complete",))

    request = tool_round_module.ToolRoundRequest(
        db="db",
        assistant_message_id="msg-itinerary",
        conversation_id="conv-itinerary",
        user_id="user-1",
        model_id="model-1",
        provider="provider-1",
        content_blocks=state.content_blocks,
        messages=[{"role": "user", "content": "8月1日从深圳去上海，比较飞机和高铁"}],
        tool_calls=tool_calls,
        reasoning_buf="",
        should_use_reasoning=False,
        step_context=AgentStepContext(
            step_id="step-1",
            run_id="run-1",
            step_number=1,
            started_at=1.0,
            thinking_block_id="blk-thinking",
            text_block_id="blk-text",
        ),
        step_number=1,
        run_id="run-1",
        emitter=emitter,
        session_cache=object(),
        network_budget=Mock(max_tool_calls=20, completed_tool_calls=0),
        call_kwargs={},
        persist_message_fn=persist_message_fn,
        execute_tools_fn=execute_tools_fn,
        complete_step_fn=complete_step_fn,
        agent_state=state,
        resolve_tool_context_fn=AsyncMock(return_value=ToolContextResolution(executable_calls=tool_calls)),
        structured_answers=PRODUCT_STRUCTURED_ANSWERS,
    )
    return request, state, order


async def _check_ticket_query_round_only_publishes_search_results():
    train_call = {
        "id": "tc-train",
        "name": "search_trains",
        "arguments": '{"origin":"深圳","destination":"上海","departure_date":"2026-08-01"}',
    }
    request, state, order = _request([_record(tool_call=train_call, status="success", block=_train_block())])

    await handle_tool_calls_round(request=request)

    # 只查票时服务端不再把结果汇总成行程卡片，完整班次列表照常展示。
    assert [block.type for block in state.content_blocks if block.type.endswith("_results")] == ["train_results"]
    assert [item[2].type for item in order if item[0] == "emit"] == ["train_results"]
    assert not any(item[0] == "discard" for item in order)


async def _present_record(tool_call: dict, state: AgentLoopState) -> ToolExecutionRecord:
    handler = PresentItineraryHandler()
    args = json.loads(tool_call["arguments"])
    result = await handler.execute(args, ToolRuntimeContext(content_blocks=tuple(state.content_blocks)))
    return ToolExecutionRecord(
        tool_call=tool_call,
        result=result,
        handler=handler,
        block_id="blk_itinerary_selected",
        log_id="log-present",
    )


async def _check_present_itinerary_round_checkpoints_and_publishes_card():
    state = AgentLoopState(content_blocks=[_train_block()])
    present_call = {
        "id": "tc-present",
        "name": "present_itinerary",
        "arguments": (
            '{"title":"深圳→上海","plans":[{"title":"高铁直达","reason":"白天出发，当天下午到",'
            '"option_ids":["train-outbound-1"]}]}'
        ),
    }
    record = await _present_record(present_call, state)
    request, _, order = _request([record], state=state)

    await handle_tool_calls_round(request=request)

    itinerary = next(block for block in state.content_blocks if isinstance(block, SelectedItineraryBlock))
    assert itinerary.plans[0].legs[0].block_id == "blk-train-outbound"
    assert itinerary.tool_call_log_id == "log-present"
    final_checkpoint = max(index for index, item in enumerate(order) if item[0] == "persist")
    emits = [(index, item) for index, item in enumerate(order) if item[0] == "emit"]
    assert [item[2].type for _, item in emits] == ["itinerary_results"]
    assert all(final_checkpoint < index for index, _ in emits)
    assert any(isinstance(block, SelectedItineraryBlock) for block in order[final_checkpoint][2])


async def _check_failed_present_itinerary_does_not_suggest_other_tools():
    state = AgentLoopState(content_blocks=[_train_block()])
    present_call = {
        "id": "tc-present",
        "name": "present_itinerary",
        "arguments": '{"title":"深圳→上海","plans":[{"title":"方案","reason":"理由","option_ids":["opt_missing"]}]}',
    }
    record = await _present_record(present_call, state)
    request, _, order = _request([record], state=state)
    request.call_kwargs["tools"] = [{"type": "function", "function": {"name": "web_search"}}]

    await handle_tool_calls_round(request=request)

    tool_message = next(item for item in request.messages if item.get("role") == "tool")
    assert "opt_missing" in tool_message["content"]
    assert "Other announced tools" not in tool_message["content"]
    assert not any(isinstance(block, SelectedItineraryBlock) for block in state.content_blocks)
    assert not any(item[0] == "emit" for item in order)


async def _check_tool_round_records_observations_and_place_choices():
    weather_call = {"id": "tc-weather", "name": "weather_forecast", "arguments": '{"location":"上海"}'}
    train_call = {
        "id": "tc-train",
        "name": "search_trains",
        "arguments": '{"origin":"深圳","destination":"上海","departure_date":"2026-08-01"}',
    }
    place_call = {"id": "tc-place", "name": "place_search", "arguments": '{"keywords":"外滩"}'}
    choice = {"place_name": "外滩", "candidates": [{"candidate": 1}, {"candidate": 2}]}
    records = [
        ToolExecutionRecord(
            tool_call=weather_call,
            result=ToolResult(
                status="failed",
                data={"error_code": "arguments_schema_invalid", "argument_errors": ["location:required"]},
            ),
            handler=None,
            block_id="blk-weather",
            log_id="log-weather",
        ),
        _record(tool_call=train_call, status="success", block=_train_block()),
        ToolExecutionRecord(
            tool_call=place_call,
            result=ToolResult(
                status="failed",
                data={"error_code": "ambiguous_location", "error_details": choice},
            ),
            handler=None,
            block_id="blk-place",
            log_id="log-place",
        ),
    ]
    request, state, _ = _request(records)

    await handle_tool_calls_round(request=request)

    assert {item.tool_name for item in state.itinerary_tool_observations} >= {"search_trains"}
    # 等用户选地名不算工具故障；参数错误照常记为失败，结果交给模型处理。
    assert "place_search" not in state.failed_tool_names
    assert "weather_forecast" in state.failed_tool_names


def test_tool_round_records_observations_and_place_choices():
    asyncio.run(_check_tool_round_records_observations_and_place_choices())


def test_ticket_query_round_only_publishes_search_results():
    asyncio.run(_check_ticket_query_round_only_publishes_search_results())


def test_present_itinerary_round_checkpoints_and_publishes_card():
    asyncio.run(_check_present_itinerary_round_checkpoints_and_publishes_card())


def test_failed_present_itinerary_does_not_suggest_other_tools():
    asyncio.run(_check_failed_present_itinerary_does_not_suggest_other_tools())
