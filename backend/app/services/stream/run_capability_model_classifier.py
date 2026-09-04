"""受预算约束的 Run 能力模型分类器。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

import litellm
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.llm_observability import merge_litellm_kwargs
from app.core.config import settings
from app.core.logger import app_logger as logger
from app.services.stream.run_capability_router import _CandidateRoute, _classify_literal_layer, _extract_request_signals
from app.utils.run_capability_contract import CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES

ClassifierResultCallback = Callable[[str, str | None], None]

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
_HARD_TIMEOUT_SECONDS = 1.5
_HARD_MAX_INPUT_TOKENS = 2000
_HARD_MAX_OUTPUT_TOKENS = 128
_HARD_CONTEXT_TURNS = 1
_ROUTE_DETAILS = {
    "direct": ("high", ("stable_knowledge_question",), False, "routed"),
    "transform": ("high", ("text_transform_request",), False, "routed"),
    "date": ("high", ("current_date_question",), True, "routed"),
    "fresh_web": ("high", ("fresh_external_fact",), True, "routed"),
    "verified_web": ("high", ("verified_source_request",), True, "routed"),
    "url_read": ("high", ("explicit_url_read",), False, "routed"),
    "weather": ("high", ("explicit_weather_request",), True, "routed"),
    "place_discovery": ("high", ("explicit_place_discovery",), False, "routed"),
    "mobility_route": ("high", ("explicit_route_task",), False, "routed"),
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
    explicit_tool_names: list[str] = Field()


@dataclass(frozen=True)
class _ClassifierLimits:
    timeout_seconds: float
    max_input_tokens: int
    max_output_tokens: int
    context_turns: int


class ClassifierDeadlineGate:
    """协调 worker 与 Runner 的 deadline 胜负和延迟分类观测。"""

    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._started_at = clock()
        self._expired = False
        self._published = False
        self._pending: tuple[str, str, float, str | None, ClassifierResultCallback | None] | None = None
        self._result_callback: ClassifierResultCallback | None = None

    def register_callback(self, result_callback: ClassifierResultCallback | None) -> None:
        if result_callback is None:
            return
        with self._lock:
            if self._result_callback is None:
                self._result_callback = result_callback

    def is_expired(self) -> bool:
        with self._lock:
            return self._expired

    def try_begin_model_call(self) -> bool:
        """模型调用的线性化点：deadline 先赢时禁止产生新调用。"""

        with self._lock:
            if self._expired or self._published:
                return False
            return True

    def buffer_observation(
        self,
        result: str,
        package_id: str,
        started_at: float,
        *,
        error_type: str | None,
        result_callback: ClassifierResultCallback | None,
    ) -> None:
        self.register_callback(result_callback)
        with self._lock:
            if self._expired or self._published:
                return
            self._pending = (result, package_id, started_at, error_type, result_callback)

    def commit_observation(self) -> bool:
        """仅当 Runner 接受完整 call-config 后公布 worker 分类结果。"""

        with self._lock:
            if self._expired or self._published or self._pending is None:
                return False
            pending = self._pending
            self._pending = None
            self._published = True
        result, package_id, started_at, error_type, result_callback = pending
        _emit_result(result_callback, result, error_type)
        _log_result(result, package_id, started_at, error_type=error_type)
        return True

    def expire_and_publish_deadline(self) -> bool:
        """outer deadline 获胜时丢弃 worker 暂存结果并公布唯一失败观测。"""

        with self._lock:
            if self._published:
                return False
            self._expired = True
            self._pending = None
            self._published = True
            result_callback = self._result_callback
        _emit_result(result_callback, "failed", "deadline_exceeded")
        _log_result(
            "failed",
            "clarification_only",
            self._started_at,
            error_type="deadline_exceeded",
            ended_at=self._clock(),
        )
        return True

    def expire(self) -> None:
        """测试或外层调度可先让 deadline 赢得模型调用准入。"""

        with self._lock:
            self._expired = True
            self._pending = None


def classify_capability_request_with_model(
    message: str,
    available_tools: list[str] | None = None,
    conversation_messages: list[object] | None = None,
    *,
    available_tool_names: list[str] | None = None,
    task_context_messages: list[object] | None = None,
    token_counter_fn: Callable[..., int] | None = None,
    result_callback: ClassifierResultCallback | None = None,
    deadline_event: threading.Event | None = None,
    deadline_gate: ClassifierDeadlineGate | None = None,
    suppress_deadline_observation: bool = False,
) -> _CandidateRoute:
    """先处理可确定的字面请求，再以一次结构化模型调用分类其余请求。"""

    started_at = perf_counter()
    if deadline_gate is not None:
        deadline_gate.register_callback(result_callback)
    if _deadline_expired(deadline_event, deadline_gate):
        return _deadline_fail_closed(
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
            suppress_observation=suppress_deadline_observation,
        )
    tools = available_tools if available_tools is not None else available_tool_names
    if tools is None:
        return _fail_closed(
            "tools_missing",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )
    context_messages = conversation_messages if conversation_messages is not None else task_context_messages
    request = _extract_request_signals(message)
    literal_route = _classify_literal_layer(request, tools)
    if literal_route is not None:
        _record_result(
            "literal",
            literal_route.package_id,
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )
        return literal_route

    limits = _effective_classifier_limits()
    if limits is None:
        return _fail_closed(
            "invalid_configuration",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )
    if not _has_classifier_credentials():
        return _fail_closed(
            "credentials_missing",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )

    model_messages = _build_messages(
        message,
        tools,
        context_messages,
        token_counter_fn=token_counter_fn,
        limits=limits,
    )
    if _deadline_expired(deadline_event, deadline_gate):
        return _deadline_fail_closed(
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
            suppress_observation=suppress_deadline_observation,
        )
    if model_messages is None:
        return _fail_closed(
            "input_budget_exceeded",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )

    try:
        completion_kwargs = {
            "model": f"litellm_proxy/{settings.RUN_CAPABILITY_CLASSIFIER_MODEL}",
            "messages": model_messages,
            "timeout": limits.timeout_seconds,
            "num_retries": 0,
            "max_tokens": limits.max_output_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            **merge_litellm_kwargs(
                "run_capability_classifier",
                {
                    "api_key": settings.LITELLM_API_KEY,
                    "api_base": settings.LITELLM_PROXY_URL,
                },
            ),
        }
        if deadline_gate is not None and not deadline_gate.try_begin_model_call():
            return _deadline_fail_closed(
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
                suppress_observation=suppress_deadline_observation,
            )
        if _deadline_expired(deadline_event, deadline_gate):
            return _deadline_fail_closed(
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
                suppress_observation=suppress_deadline_observation,
            )
        response = litellm.completion(**completion_kwargs)
        route = _parse_model_route(
            response,
            request.all_network_denied,
            tools,
            include_current_date=request.include_current_date,
        )
    except (Exception, ValidationError, ValueError, TypeError) as exc:
        if _deadline_expired(deadline_event, deadline_gate):
            return _deadline_fail_closed(
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
                suppress_observation=suppress_deadline_observation,
            )
        return _fail_closed(
            _error_type(exc),
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )
    if _deadline_expired(deadline_event, deadline_gate):
        return _deadline_fail_closed(
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
            suppress_observation=suppress_deadline_observation,
        )
    if route is None:
        return _fail_closed(
            "invalid_response",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )
    _record_result(
        "model",
        route.package_id,
        started_at,
        result_callback=result_callback,
        observation_gate=deadline_gate,
    )
    return route


def _build_messages(
    message: str,
    available_tools: list[str],
    conversation_messages: list[object] | None,
    *,
    token_counter_fn: Callable[..., int] | None = None,
    limits: _ClassifierLimits | None = None,
) -> list[dict[str, str]] | None:
    effective_limits = limits or _effective_classifier_limits()
    if effective_limits is None:
        return None
    current_message = str(message)
    history = _most_recent_complete_turn(conversation_messages, context_turns=effective_limits.context_turns)
    system_message = {
        "role": "system",
        "content": _system_prompt(),
    }
    messages = [system_message, *history, {"role": "user", "content": current_message}]
    if _within_input_budget(messages, token_counter_fn, effective_limits.max_input_tokens):
        return messages
    if not history:
        return None
    messages = [system_message, {"role": "user", "content": current_message}]
    if _within_input_budget(messages, token_counter_fn, effective_limits.max_input_tokens):
        return messages
    return None


def _system_prompt() -> str:
    return """将当前请求分类为一个 package_id。只输出 JSON 对象，且必须同时包含 package_id 和 explicit_tool_names 两个字段；不得有额外字段。
固定 taxonomy 与工具映射（explicit_tool_names 必须完全匹配，按 canonical order）：
- direct：问候、身份、稳定常识或简单计算；[]。
- transform：翻译、改写、润色或已给文本摘要；[]。
- date：只问当前日期或星期；[]。
- fresh_web：最新或当前外部事实、新闻、公开发布；[web_search]。
- verified_web：要求官方或可靠来源、查证；[web_search,url_read]。
- url_read：给定 URL 的读取或总结；[url_read]。
- weather：明确天气、气温、降水或风力；[weather_forecast]。
- place_discovery：附近地点、餐厅、酒店、景点发现；[local_place_search]。
- mobility_route：明确同城路线、公交、驾车、步行或通勤；[route_compare]。
- flight：明确航班、飞机或机票；[search_flights]。
- train：明确高铁、动车、火车或车次；[search_trains]。
- travel_air_rail：仅比较航班与火车；[search_flights,search_trains]。
- mobility_intercity：跨城起终点但方式不明确；[route_compare,search_flights,search_trains]。
- mixed_itinerary：2–3 个不同产品族；只可为天气、地点、路线、航班、火车的组合，且不能仅为航班加火车。
- clarification_only：能力不明、关键实体不足、冲突或不符合以上规则；[]。
canonical order 固定为 web_search,url_read,weather_forecast,local_place_search,route_compare,search_flights,search_trains。
标准 package 要表达请求实际需要的能力，不得因当前 definitions 或 available_tool_names 缺少标准产品工具而改选 clarification_only；实际可用性、禁工具、无 function calling 与 knowledge-grounded 降级由后续 resolver 决定。
available_tool_names 只用于本调用前的精确 MCP literal 授权，不能作为标准 package 工具可用性的依据。禁止选择 deep_research、knowledge_grounded、tools_unavailable 或 mcp_explicit。全局禁网时不得选择任何外部工具；包与工具不匹配或不确定时选择 clarification_only。"""


def _most_recent_complete_turn(
    messages: Sequence[object] | None,
    *,
    context_turns: int | None = None,
) -> list[dict[str, str]]:
    effective_context_turns = context_turns
    if effective_context_turns is None:
        limits = _effective_classifier_limits()
        effective_context_turns = limits.context_turns if limits is not None else 0
    if not messages or effective_context_turns < 1:
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
    role = _message_field(value, "role")
    content = _message_field(value, "content")
    text_content = _project_text_content(content)
    if role not in {"user", "assistant"} or text_content is None:
        return None
    return {"role": role, "content": text_content}


def _message_field(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _project_text_content(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return None
    text_blocks = []
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            text_blocks.append(text)
    return "\n".join(text_blocks) if text_blocks else None


def _within_input_budget(
    messages: Sequence[Mapping[str, str]],
    token_counter_fn: Callable[..., int] | None,
    max_input_tokens: int,
) -> bool:
    try:
        tokenizer_model = _token_counter_model()
        if tokenizer_model is None:
            return False
        token_count = (token_counter_fn or litellm.token_counter)(
            model=tokenizer_model,
            messages=list(messages),
        )
    except Exception:
        return False
    return isinstance(token_count, int) and not isinstance(token_count, bool) and token_count <= max_input_tokens


def _token_counter_model() -> str | None:
    model = settings.RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL
    if not isinstance(model, str) or not model.strip():
        return None
    return model.strip()


def _effective_classifier_limits() -> _ClassifierLimits | None:
    timeout_seconds = _positive_capped_float(
        settings.RUN_CAPABILITY_CLASSIFIER_TIMEOUT_SECONDS,
        _HARD_TIMEOUT_SECONDS,
    )
    max_input_tokens = _positive_capped_int(
        settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS,
        _HARD_MAX_INPUT_TOKENS,
    )
    max_output_tokens = _positive_capped_int(
        settings.RUN_CAPABILITY_CLASSIFIER_MAX_OUTPUT_TOKENS,
        _HARD_MAX_OUTPUT_TOKENS,
    )
    context_turns = _positive_capped_int(
        settings.RUN_CAPABILITY_CLASSIFIER_CONTEXT_TURNS,
        _HARD_CONTEXT_TURNS,
    )
    if None in (timeout_seconds, max_input_tokens, max_output_tokens, context_turns):
        return None
    return _ClassifierLimits(
        timeout_seconds=timeout_seconds,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        context_turns=context_turns,
    )


def _positive_capped_float(value: object, upper_bound: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        return None
    return min(normalized, upper_bound)


def _positive_capped_int(value: object, upper_bound: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return min(value, upper_bound)


def _parse_model_route(
    response: object,
    all_network_denied: bool,
    _available_tools: list[str],
    *,
    include_current_date: bool,
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
            or set(explicit_tools) == {"search_flights", "search_trains"}
        ):
            return None
    elif explicit_tools != allowed_tools:
        return None
    if all_network_denied and explicit_tools:
        return None
    confidence, reason_codes, fixed_include_current_date, resolution_mode = _ROUTE_DETAILS[package_id]
    resolved_include_current_date = fixed_include_current_date
    if package_id == "mobility_route":
        resolved_include_current_date = include_current_date
    canonical_tools = tuple(name for name in _CANONICAL_TOOL_ORDER if name in explicit_tools)
    return _CandidateRoute(
        package_id=package_id,
        confidence=confidence,
        reason_codes=reason_codes,
        include_current_date=resolved_include_current_date,
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


def _fail_closed(
    error_type: str,
    started_at: float,
    *,
    result_callback: ClassifierResultCallback | None = None,
    observation_gate: ClassifierDeadlineGate | None = None,
) -> _CandidateRoute:
    _record_result(
        "failed",
        "clarification_only",
        started_at,
        error_type=error_type,
        result_callback=result_callback,
        observation_gate=observation_gate,
    )
    return _CandidateRoute(
        package_id="clarification_only",
        confidence="low",
        reason_codes=("insufficient_capability_signal",),
        include_current_date=False,
        resolution_mode="clarification",
    )


def _deadline_fail_closed(
    started_at: float,
    *,
    result_callback: ClassifierResultCallback | None,
    observation_gate: ClassifierDeadlineGate | None,
    suppress_observation: bool,
) -> _CandidateRoute:
    if not suppress_observation:
        return _fail_closed(
            "deadline_exceeded",
            started_at,
            result_callback=result_callback,
            observation_gate=observation_gate,
        )
    return _CandidateRoute(
        package_id="clarification_only",
        confidence="low",
        reason_codes=("insufficient_capability_signal",),
        include_current_date=False,
        resolution_mode="clarification",
    )


def _deadline_expired(
    deadline_event: threading.Event | None,
    deadline_gate: ClassifierDeadlineGate | None,
) -> bool:
    return (deadline_event is not None and deadline_event.is_set()) or (
        deadline_gate is not None and deadline_gate.is_expired()
    )


def _record_result(
    result: str,
    package_id: str,
    started_at: float,
    *,
    result_callback: ClassifierResultCallback | None,
    observation_gate: ClassifierDeadlineGate | None,
    error_type: str | None = None,
) -> None:
    if observation_gate is not None:
        observation_gate.buffer_observation(
            result,
            package_id,
            started_at,
            error_type=error_type,
            result_callback=result_callback,
        )
        return
    _emit_result(result_callback, result, error_type)
    _log_result(result, package_id, started_at, error_type=error_type)


def _emit_result(
    result_callback: ClassifierResultCallback | None,
    result: str,
    error_type: str | None = None,
) -> None:
    if result_callback is None:
        return
    try:
        result_callback(result, error_type)
    except Exception:
        return


def _error_type(error: BaseException) -> str:
    if isinstance(error, (TimeoutError, litellm.Timeout)):
        return "timeout"
    if isinstance(error, ValidationError):
        return "validation_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    return "call_error"


def _log_result(
    result: str,
    package_id: str,
    started_at: float,
    *,
    error_type: str | None = None,
    ended_at: float | None = None,
) -> None:
    logger.info(
        "run_capability_classifier result=%s package_id=%s duration_ms=%s error_type=%s",
        result,
        package_id,
        max(0, int(((perf_counter() if ended_at is None else ended_at) - started_at) * 1000)),
        error_type,
    )
