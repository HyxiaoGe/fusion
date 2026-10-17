"""产品工具（出行、天气、路线）在工具轮之后的结果记录与行程汇总。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.schemas.chat import (
    FlightResultsBlock,
    ItineraryResultsBlock,
    RouteResultsBlock,
    TrainResultsBlock,
    WeatherResultsBlock,
)
from app.services.stream.agent_loop_state import AgentLoopState, ProductToolOutcome
from app.services.stream.itinerary_observability import build_itinerary_tool_observation
from app.services.stream.itinerary_result_composer import compose_itinerary_result
from app.services.stream.tool_execution_result import ToolExecutionRecord


def record_itinerary_tool_observations(
    state: AgentLoopState,
    results: list[ToolExecutionRecord],
) -> None:
    observations = [
        observation for record in results if (observation := build_itinerary_tool_observation(record)) is not None
    ]
    state.record_itinerary_tool_observations(observations)


def build_product_tool_outcomes(
    results: list[ToolExecutionRecord],
    *,
    built_content_blocks: dict[str, Any],
) -> tuple[ProductToolOutcome, ...]:
    outcomes: list[ProductToolOutcome] = []
    for record in results:
        if record.reused:
            continue
        tool_call_id = str(record.tool_call.get("id", ""))
        content_block = built_content_blocks.get(tool_call_id)
        outcome = _build_product_tool_outcome(record, content_block)
        if outcome is not None:
            outcomes.append(outcome)
    return tuple(outcomes)


def _build_product_tool_outcome(
    record: ToolExecutionRecord,
    content_block: Any,
) -> ProductToolOutcome | None:
    tool_name = record.tool_name
    mode = {
        "search_flights": "flight",
        "search_trains": "train",
        "weather_forecast": "weather",
        "route_compare": "route",
    }.get(tool_name)
    if mode is None or _has_retryable_product_repair(record):
        return None
    if isinstance(content_block, (FlightResultsBlock, TrainResultsBlock)):
        return ProductToolOutcome(
            tool_name=tool_name,
            mode=mode,
            status="available",
            block_id=content_block.id,
            origin=content_block.origin,
            destination=content_block.destination,
            departure_date=content_block.departure_date,
        )
    if isinstance(content_block, WeatherResultsBlock):
        return ProductToolOutcome(
            tool_name=tool_name,
            mode="weather",
            status="available",
            block_id=content_block.id,
            location=content_block.resolved_location,
        )
    if isinstance(content_block, RouteResultsBlock):
        return ProductToolOutcome(
            tool_name=tool_name,
            mode="route",
            status="available",
            block_id=content_block.id,
            origin=content_block.origin.label,
            destination=content_block.destination.label,
        )
    return _unavailable_product_tool_outcome(record, mode)


def _unavailable_product_tool_outcome(
    record: ToolExecutionRecord,
    mode: str,
) -> ProductToolOutcome | None:
    arguments = _safe_tool_arguments(record.tool_call)
    if mode in {"flight", "train"}:
        origin = _safe_argument_text(arguments.get("origin"), 80)
        destination = _safe_argument_text(arguments.get("destination"), 80)
        departure_date = _safe_departure_date(arguments.get("departure_date"))
        if not origin or not destination or departure_date is None:
            return None
        return ProductToolOutcome(
            tool_name=record.tool_name,
            mode=mode,
            status="unavailable",
            origin=origin,
            destination=destination,
            departure_date=departure_date,
        )
    if mode == "weather":
        location = _safe_argument_text(arguments.get("location"), 120)
        return (
            ProductToolOutcome(
                tool_name=record.tool_name,
                mode="weather",
                status="unavailable",
                location=location,
            )
            if location
            else None
        )
    origin = _safe_argument_text(arguments.get("origin"), 120)
    destination = _safe_argument_text(arguments.get("destination"), 120)
    if not origin or not destination:
        return None
    return ProductToolOutcome(
        tool_name=record.tool_name,
        mode="route",
        status="unavailable",
        origin=origin,
        destination=destination,
    )


def _has_retryable_product_repair(record: ToolExecutionRecord) -> bool:
    data = record.result.data if isinstance(record.result.data, dict) else {}
    repair = data.get("repair")
    return isinstance(repair, dict) and repair.get("retryable") is True


def _safe_tool_arguments(tool_call: dict) -> dict[str, Any]:
    raw_arguments = tool_call.get("arguments")
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_argument_text(value: Any, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= max_chars else None


def _safe_departure_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def upsert_itinerary_result(
    content_blocks: list[Any],
    *,
    product_outcomes: list[ProductToolOutcome],
) -> ItineraryResultsBlock | None:
    itinerary = compose_itinerary_result(content_blocks, product_outcomes=product_outcomes)
    if itinerary is None:
        content_blocks[:] = [block for block in content_blocks if not isinstance(block, ItineraryResultsBlock)]
        return None
    existing_index = next(
        (index for index, block in enumerate(content_blocks) if isinstance(block, ItineraryResultsBlock)),
        None,
    )
    if existing_index is None:
        content_blocks.append(itinerary)
        return itinerary
    if content_blocks[existing_index] == itinerary:
        return None
    content_blocks[existing_index] = itinerary
    return itinerary
