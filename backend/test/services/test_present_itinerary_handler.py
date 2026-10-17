from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.schemas.chat import (
    FlightOption,
    FlightResultsBlock,
    SelectedItineraryBlock,
    TrainOption,
    TrainResultsBlock,
    TravelEndpoint,
    TravelMoney,
)
from app.schemas.content_block_registry import deserialize_content_blocks
from app.services.tool_handlers.base import ToolRuntimeContext
from app.services.tool_handlers.itinerary import PresentItineraryHandler, build_present_itinerary_tool

TZ = ZoneInfo("Asia/Shanghai")


def _endpoint(city: str, station: str, day: int, hour: int) -> TravelEndpoint:
    return TravelEndpoint(city=city, station_name=station, scheduled_at=datetime(2026, 8, day, hour, tzinfo=TZ))


def _flights(block_id: str = "blk-flight-out") -> FlightResultsBlock:
    return FlightResultsBlock(
        type="flight_results",
        id=block_id,
        schema_version=1,
        provider="flyai",
        status="success",
        origin="上海",
        destination="北京",
        departure_date="2026-08-01",
        observed_at=datetime(2026, 7, 26, 8, tzinfo=TZ),
        result_count=2,
        flights=[
            FlightOption(
                option_id="opt_out_early",
                flight_no="MU5101",
                departure=_endpoint("上海", "虹桥机场", 1, 8),
                arrival=_endpoint("北京", "首都机场", 1, 10),
                duration_s=7_200,
                stops=0,
                price=TravelMoney(currency="CNY", amount_minor=88_000),
            ),
            FlightOption(
                option_id="opt_out_late",
                flight_no="CA1858",
                departure=_endpoint("上海", "虹桥机场", 1, 19),
                arrival=_endpoint("北京", "首都机场", 1, 21),
                duration_s=7_200,
                stops=0,
            ),
        ],
    )


def _return_trains() -> TrainResultsBlock:
    return TrainResultsBlock(
        type="train_results",
        id="blk-train-back",
        schema_version=1,
        provider="flyai",
        status="success",
        origin="北京",
        destination="上海",
        departure_date="2026-08-03",
        observed_at=datetime(2026, 7, 26, 8, tzinfo=TZ),
        result_count=1,
        trains=[
            TrainOption(
                option_id="opt_back_g1",
                train_no="G1",
                departure=_endpoint("北京", "北京南站", 3, 9),
                arrival=_endpoint("上海", "上海虹桥站", 3, 13),
                duration_s=14_400,
                stops=0,
                price=TravelMoney(currency="CNY", amount_minor=62_000),
            )
        ],
    )


def _run(args: dict, blocks: list) -> tuple[PresentItineraryHandler, object]:
    handler = PresentItineraryHandler()
    result = asyncio.run(handler.execute(args, ToolRuntimeContext(content_blocks=tuple(blocks))))
    return handler, result


def _plan(*option_ids: str, title: str = "方案") -> dict:
    return {"title": title, "reason": "早去晚回，时间充裕", "option_ids": list(option_ids)}


def test_model_selected_round_trip_becomes_card_referencing_returned_services():
    blocks = [_flights(), _return_trains()]
    handler, result = _run(
        {
            "title": "上海⇄北京",
            "plans": [_plan("opt_out_early", "opt_back_g1", title="去飞回铁"), _plan("opt_out_late")],
        },
        blocks,
    )

    assert result.status == "success"
    block = handler.build_content_block(result, "blk_itinerary_1", "log-1")
    assert isinstance(block, SelectedItineraryBlock)
    assert block.schema_version == 2
    assert [(leg.block_id, leg.option_id) for leg in block.plans[0].legs] == [
        ("blk-flight-out", "opt_out_early"),
        ("blk-train-back", "opt_back_g1"),
    ]
    assert [plan.title for plan in block.plans] == ["去飞回铁", "方案"]
    # 历史恢复时引用仍能解析，卡片照常展示。
    restored = deserialize_content_blocks([item.model_dump(mode="json") for item in [*blocks, block]])
    assert isinstance(restored[-1], SelectedItineraryBlock)
    context = handler.format_llm_context(result)
    assert "2 plan(s)" in context and "去飞回铁" in context


def test_unknown_option_ids_are_returned_to_model_without_card():
    handler, result = _run({"title": "行程", "plans": [_plan("opt_out_early", "opt_made_up")]}, [_flights()])

    assert result.status == "failed"
    assert result.data["error_code"] == "unknown_option_ids"
    assert handler.build_content_block(result, "blk", "log") is None
    assert "opt_made_up" in handler.format_llm_context(result)


def test_services_must_connect_in_travel_order():
    _, result = _run(
        {"title": "行程", "plans": [_plan("opt_back_g1", "opt_out_early")]}, [_flights(), _return_trains()]
    )
    assert result.data["error_code"] == "legs_out_of_order"

    _, connecting = _run({"title": "行程", "plans": [_plan("opt_out_early", "opt_out_late")]}, [_flights()])
    # 早班 10 点落地、晚班 19 点起飞，时间上能衔接；是否合理由模型说明，服务端不判断。
    assert connecting.status == "success"


def test_duplicate_service_and_second_card_are_rejected():
    _, duplicate = _run({"title": "行程", "plans": [_plan("opt_out_early", "opt_out_early")]}, [_flights()])
    assert duplicate.data["error_code"] == "duplicate_option_ids"

    handler, first = _run({"title": "行程", "plans": [_plan("opt_out_early")]}, [_flights()])
    card = handler.build_content_block(first, "blk_itinerary_1", "log-1")
    _, second = _run({"title": "行程", "plans": [_plan("opt_out_late")]}, [_flights(), card])
    assert second.data["error_code"] == "already_presented"


def test_invalid_arguments_and_tool_schema_limits():
    _, result = _run({"title": "行程", "plans": []}, [_flights()])
    assert result.data["error_code"] == "invalid_arguments"

    schema = build_present_itinerary_tool()["function"]["parameters"]
    assert schema["properties"]["plans"]["maxItems"] == 3
    assert schema["properties"]["plans"]["items"]["properties"]["option_ids"]["maxItems"] == 6


def test_repeated_query_references_latest_result_block():
    _, result = _run({"title": "行程", "plans": [_plan("opt_out_early")]}, [_flights("blk-old"), _flights("blk-new")])
    assert result.data["plans"][0]["legs"] == [{"block_id": "blk-new", "option_id": "opt_out_early"}]


def test_history_card_with_missing_reference_is_not_rendered():
    handler, result = _run({"title": "行程", "plans": [_plan("opt_out_early")]}, [_flights()])
    card = handler.build_content_block(result, "blk_itinerary_1", "log-1")

    restored = deserialize_content_blocks([card.model_dump(mode="json")])

    assert restored[0].type == "unsupported_result"
