"""受预算约束的 Run 能力模型分类器。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from math import ceil
from time import perf_counter

import litellm
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.llm_observability import merge_litellm_kwargs
from app.core.config import settings
from app.core.logger import app_logger as logger
from app.services.stream.run_capability_router import _CandidateRoute, _classify_literal_layer, _extract_request_signals
from app.utils.run_capability_contract import CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES

_MODEL_PACKAGE_IDS = frozenset(
    {
        "direct",
        "transform",
        "date",
        "fresh_web",
        "verified_web",
        "url_read",
        "weather",
        "place_discovery",
        "mobility_route",
        "flight",
        "train",
        "travel_air_rail",
        "mobility_intercity",
        "mixed_itinerary",
        "clarification_only",
    }
)
_MIXED_ITINERARY_TOOLS = frozenset(
    {"weather_forecast", "local_place_search", "route_compare", "search_flights", "search_trains"}
)
_CANONICAL_TOOL_ORDER = (
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
)
_ROUTE_DETAILS = {
    "direct": ("high", ("stable_knowledge_question",), False, "routed"),
    "transform": ("high", ("text_transform_request",), False, "routed"),
    "date": ("high", ("current_date_question",), True, "routed"),
    "fresh_web": ("high", ("fresh_external_fact",), True, "routed"),
    "verified_web": ("high", ("verified_source_request",), True, "routed"),
    "url_read": ("high", ("explicit_url_read",), False, "routed"),
    "weather": ("high", ("explicit_weather_request",), True, "routed"),
    "place_discovery": ("high", ("explicit_place_discovery",), False, "routed"),
    "mobility_route": ("high", ("explicit_route_task",), True, "routed"),
    "flight": ("high", ("explicit_flight_request",), True, "routed"),
    "train": ("high", ("explicit_train_request",), True, "routed"),
    "travel_air_rail": ("high", ("air_rail_comparison",), True, "routed"),
    "mobility_intercity": ("medium", ("origin_destination_relation", "intercity_locations"), True, "routed"),
    "mixed_itinerary": ("high", ("mixed_itinerary_request",), True, "routed"),
    "clarification_only": ("low", ("insufficient_capability_signal",), False, "clarification"),
}


class _ModelRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str
    explicit_tool_names: list[str] = Field(default_factory=list)


def classify_capability_request_with_model(
    message: str,
    available_tools: list[str] | None = None,
    conversation_messages: list[object] | None = None,
    *,
    available_tool_names: list[str] | None = None,
    task_context_messages: list[object] | None = None,
) -> _CandidateRoute:
    """先处理可确定的字面请求，再以一次结构化模型调用分类其余请求。"""

    started_at = perf_counter()
    tools = available_tools if available_tools is not None else available_tool_names
    if tools is None:
        return _fail_closed("tools_missing", started_at)
    context_messages = conversation_messages if conversation_messages is not None else task_context_messages
    request = _extract_request_signals(message)
    literal_route = _classify_literal_layer(request)
    if literal_route is not None:
        _log_result("literal", literal_route.package_id, started_at)
        return literal_route

    if not _has_classifier_credentials():
        return _fail_closed("credentials_missing", started_at)

    model_messages = _build_messages(message, tools, context_messages)
    if model_messages is None:
        return _fail_closed("input_budget_exceeded", started_at)

    try:
        response = litellm.completion(
            model=f"litellm_proxy/{settings.RUN_CAPABILITY_CLASSIFIER_MODEL}",
            messages=model_messages,
            timeout=settings.RUN_CAPABILITY_CLASSIFIER_TIMEOUT_SECONDS,
            num_retries=0,
            max_tokens=settings.RUN_CAPABILITY_CLASSIFIER_MAX_OUTPUT_TOKENS,
            temperature=0,
            response_format={"type": "json_object"},
            **merge_litellm_kwargs(
                "run_capability_classifier",
                {
                    "api_key": settings.LITELLM_API_KEY,
                    "api_base": settings.LITELLM_PROXY_URL,
                },
            ),
        )
        route = _parse_model_route(response, request.all_network_denied, tools)
    except (Exception, ValidationError, ValueError, TypeError) as exc:
        return _fail_closed(_error_type(exc), started_at)
    if route is None:
        return _fail_closed("invalid_response", started_at)
    _log_result("model", route.package_id, started_at)
    return route


def _build_messages(
    message: str,
    available_tools: list[str],
    conversation_messages: list[object] | None,
) -> list[dict[str, str]] | None:
    current_message = str(message)
    history = _most_recent_complete_turn(conversation_messages)
    system_message = {
        "role": "system",
        "content": (
            "将请求分类为一个 package_id，并只返回 JSON："
            "package_id 与 explicit_tool_names。允许包："
            + ",".join(sorted(_MODEL_PACKAGE_IDS))
            + "。仅可选择工具："
            + ",".join(name for name in _CANONICAL_TOOL_ORDER if name in set(available_tools))
        ),
    }
    messages = [system_message, *history, {"role": "user", "content": current_message}]
    if _estimated_tokens(messages) <= settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS:
        return messages
    messages = [system_message, {"role": "user", "content": current_message}]
    if _estimated_tokens(messages) <= settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS:
        return messages
    return None


def _most_recent_complete_turn(messages: Sequence[object] | None) -> list[dict[str, str]]:
    if not messages or settings.RUN_CAPABILITY_CLASSIFIER_CONTEXT_TURNS < 1:
        return []
    normalized = [_normalize_conversation_message(message) for message in messages]
    for index in range(len(normalized) - 2, -1, -1):
        user_message = normalized[index]
        assistant_message = normalized[index + 1]
        if user_message is not None and assistant_message is not None:
            if user_message["role"] == "user" and assistant_message["role"] == "assistant":
                return [user_message, assistant_message]
    return []


def _normalize_conversation_message(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    role = value.get("role")
    content = value.get("content")
    if role not in {"user", "assistant"} or not isinstance(content, str):
        return None
    return {"role": role, "content": content}


def _estimated_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    return sum(ceil(len(message["content"]) / 4) for message in messages)


def _parse_model_route(
    response: object,
    all_network_denied: bool,
    available_tools: list[str],
) -> _CandidateRoute | None:
    parsed = _ModelRouteResponse.model_validate_json(_response_content(response))
    package_id = parsed.package_id
    if package_id not in _MODEL_PACKAGE_IDS:
        return None
    explicit_tools = tuple(parsed.explicit_tool_names)
    allowed_tools = CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES[package_id]
    if package_id == "mixed_itinerary":
        if (
            not 2 <= len(explicit_tools) <= 3
            or len(set(explicit_tools)) != len(explicit_tools)
            or not set(explicit_tools).issubset(_MIXED_ITINERARY_TOOLS)
        ):
            return None
    elif explicit_tools != allowed_tools:
        return None
    if not set(explicit_tools).issubset(set(available_tools)):
        return None
    if all_network_denied and explicit_tools:
        return None
    confidence, reason_codes, include_current_date, resolution_mode = _ROUTE_DETAILS[package_id]
    canonical_tools = tuple(name for name in _CANONICAL_TOOL_ORDER if name in explicit_tools)
    return _CandidateRoute(
        package_id=package_id,
        confidence=confidence,
        reason_codes=reason_codes,
        include_current_date=include_current_date,
        resolution_mode=resolution_mode,
        explicit_tool_names=canonical_tools or None,
    )


def _response_content(response: object) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, Sequence) or not choices:
        raise ValueError("missing_choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        raise ValueError("missing_content")
    return content


def _has_classifier_credentials() -> bool:
    return bool(
        settings.LITELLM_API_KEY
        and settings.LITELLM_PROXY_URL
        and settings.RUN_CAPABILITY_CLASSIFIER_MODEL
        and not settings.RUN_CAPABILITY_CLASSIFIER_MODEL.startswith("litellm_proxy/")
    )


def _fail_closed(error_type: str, started_at: float) -> _CandidateRoute:
    _log_result("failed", "clarification_only", started_at, error_type=error_type)
    return _CandidateRoute(
        package_id="clarification_only",
        confidence="low",
        reason_codes=("insufficient_capability_signal",),
        include_current_date=False,
        resolution_mode="clarification",
    )


def _error_type(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ValidationError):
        return "validation_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    return "call_error"


def _log_result(result: str, package_id: str, started_at: float, *, error_type: str | None = None) -> None:
    logger.info(
        "run_capability_classifier result=%s package_id=%s duration_ms=%s error_type=%s",
        result,
        package_id,
        max(0, int((perf_counter() - started_at) * 1000)),
        error_type,
    )
