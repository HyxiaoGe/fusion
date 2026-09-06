"""结构化产品结果出现后，供当前模型轮次使用的静态系统约束。"""

from __future__ import annotations

from typing import Any

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

_PRODUCT_RESULT_TYPES = {
    "place_results",
    "route_results",
    "weather_results",
    "flight_results",
    "train_results",
    "itinerary_results",
}

PRODUCT_RESULT_ROUND_BASE_PROMPT = render_runtime_prompt("product_results.base")

WEATHER_RESULT_ROUND_PROMPT = render_runtime_prompt("product_results.weather")

PLACE_RESULT_ROUND_PROMPT = render_runtime_prompt("product_results.place")

ROUTE_RESULT_ROUND_PROMPT = render_runtime_prompt("product_results.route")

TRAVEL_RESULT_ROUND_PROMPT = render_runtime_prompt("product_results.travel")

MIXED_TRAVEL_RESULT_ROUND_PROMPT = render_runtime_prompt("product_results.mixed_travel")


def build_product_result_round_prompt(content_blocks: list[Any]) -> str:
    """仅根据安全的结果类型选择静态约束，不读取或提升第三方结果正文。"""

    block_types = {
        block_type for block in content_blocks if (block_type := _block_type(block)) in _PRODUCT_RESULT_TYPES
    }
    if not block_types:
        return ""

    sections = [PRODUCT_RESULT_ROUND_BASE_PROMPT]
    if "place_results" in block_types:
        sections.append(PLACE_RESULT_ROUND_PROMPT)
    if "route_results" in block_types:
        sections.append(ROUTE_RESULT_ROUND_PROMPT)
    if "weather_results" in block_types:
        sections.append(WEATHER_RESULT_ROUND_PROMPT)
    if block_types.intersection({"flight_results", "train_results", "itinerary_results"}):
        sections.append(TRAVEL_RESULT_ROUND_PROMPT)
    if {"flight_results", "train_results"}.issubset(block_types):
        sections.append(MIXED_TRAVEL_RESULT_ROUND_PROMPT)
    return "\n\n".join(sections)


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        value = block.get("type")
    else:
        value = getattr(block, "type", None)
    return value if isinstance(value, str) else None
