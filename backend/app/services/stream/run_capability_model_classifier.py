"""受预算约束的 Run 能力模型分类器。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Literal

import litellm
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.llm_observability import merge_litellm_kwargs
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.config import settings
from app.core.logger import app_logger as logger
from app.core.prompt_snapshot import current_prompt_snapshot
from app.services.stream.run_capability_router import _CandidateRoute, classifier_unavailable_route
from app.utils.run_capability_contract import (
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_MAX_MCP_ALIASES,
    CAPABILITY_MODEL_PACKAGE_IDS,
    CAPABILITY_PACKAGES,
    CAPABILITY_RECOVERY_TOOL_NAMES,
    McpRouteTool,
    is_authorized_mcp_tool_alias,
)

ClassifierResultCallback = Callable[[str, str | None], None]

_HARD_TIMEOUT_SECONDS = 1.5
# 分类器系统提示词自身约 1550 token；输入长到 12k token 时 deepseek-chat 仍在 1s 内返回（dev 实测），
# 上限只防极端粘贴，超出时截断当前消息而不是判失败。
_HARD_MAX_INPUT_TOKENS = 16000
_HARD_MAX_OUTPUT_TOKENS = 128
_HARD_CONTEXT_TURNS = 1
# 分类器总时限（含一次修正重试或换备用模型），Runner 的 call-config 硬 deadline 与此一致。
CLASSIFIER_TOTAL_DEADLINE_SECONDS = 3.0
# 首次输出不合契约时修正一次，或首次调用失败时换备用模型一次，两者共用这一次机会。
_MAX_MODEL_ATTEMPTS = 2
# 给 deadline 之后的组装步骤留余量，剩余预算不足就不再重试。
_REPAIR_RETRY_MARGIN_SECONDS = 0.3
_REPAIR_RETRY_MIN_SECONDS = 0.5
# 截断超长消息时保留开头与结尾：用户的要求通常在这两处，中间多是粘贴的材料。
_TRUNCATION_MAX_ROUNDS = 5
_TRUNCATION_MARKER = "\n\n[……中间内容过长，已省略……]\n\n"
# 各模型关闭推理的参数互不兼容：deepseek 走 reasoning_effort，千问经 OpenAI 兼容接口
# 只认 enable_thinking（传 reasoning_effort 会被代理判为不支持的参数直接 400）。
_DEFAULT_NON_REASONING_KWARGS: dict = {"reasoning_effort": "none"}
_NON_REASONING_KWARGS_BY_MODEL: dict[str, dict] = {
    "qwen3.8-flash": {"extra_body": {"enable_thinking": False}},
}
_INVALID_ROUTE_FEEDBACK = (
    "上一次输出的能力包与工具组合不符合系统说明中的规则（能力包不存在、工具不属于该包、"
    "工具不可用或禁用列表不合法）。请重新按系统说明判断，只输出一个 JSON 对象。"
)


class _ModelRouteResponse(BaseModel):
    # 模型偶尔会把 response_format 的 {"type": "json_object"} 抄进输出；决定路由的字段仍逐项校验，无关字段忽略。
    model_config = ConfigDict(extra="ignore")

    package_id: str
    explicit_tool_names: list[str] = Field()
    network_policy: Literal["allow", "no_web_search", "no_url_read", "no_network"]
    denied_tool_names: list[str] = Field(max_length=8)
    required_primary_tool_name: str | None = None


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

    def try_begin_blocking_work(self) -> bool:
        """潜在阻塞的预处理也必须在线性化点后才可启动。"""

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
            classifier_unavailable_route().package_id,
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
    mcp_tool_catalog: tuple[McpRouteTool, ...] = (),
) -> _CandidateRoute:
    """以一次结构化模型调用分类请求，并在服务端校验工具授权。"""

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
    # 字面层短路已删除（#132）：不再在模型之前按正则预选能力包。
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

    try:
        model_messages = _build_messages(
            message,
            tools,
            context_messages,
            mcp_tool_catalog=mcp_tool_catalog,
            token_counter_fn=token_counter_fn,
            limits=limits,
            deadline_event=deadline_event,
            deadline_gate=deadline_gate,
        )
    except _TokenCountUnavailable as exc:
        if _deadline_expired(deadline_event, deadline_gate):
            return _deadline_fail_closed(
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
                suppress_observation=suppress_deadline_observation,
            )
        logger.warning("run_capability_classifier token 计数不可用 cause=%s", exc)
        return _fail_closed(
            "token_count_failed",
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
    if model_messages is None:
        return _fail_closed(
            "input_budget_exceeded",
            started_at,
            result_callback=result_callback,
            observation_gate=deadline_gate,
        )

    models = _classifier_models()
    model_index = 0
    completion_kwargs = _completion_kwargs(models[model_index], limits, limits.timeout_seconds)
    attempt_messages = model_messages
    for attempt in range(_MAX_MODEL_ATTEMPTS):
        content: str | None = None
        route: _CandidateRoute | None = None
        try:
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
            response = litellm.completion(**{**completion_kwargs, "messages": attempt_messages})
            content = _response_content(response)
            route = _parse_model_route(
                response,
                tools,
                mcp_tool_catalog=mcp_tool_catalog,
                # 日期判据已删除（#132）：不再判断“要不要给今天日期”，一律注入。
                include_current_date=True,
            )
            error_type = None if route is not None else "invalid_response"
            feedback = None if route is not None else _INVALID_ROUTE_FEEDBACK
        except ValidationError as exc:
            error_type = "validation_error"
            feedback = _validation_feedback(exc)
        except (Exception, ValueError, TypeError) as exc:
            if _deadline_expired(deadline_event, deadline_gate):
                return _deadline_fail_closed(
                    started_at,
                    result_callback=result_callback,
                    observation_gate=deadline_gate,
                    suppress_observation=suppress_deadline_observation,
                )
            # 调用本身失败（超时/代理不可用）不是输出问题，回传错误也修不好；
            # 换另一家上游的备用模型在剩余时间里再分一次，仍不行才兜底。
            fallback_timeout = _retry_timeout(started_at, limits)
            if model_index + 1 < len(models) and attempt + 1 < _MAX_MODEL_ATTEMPTS and fallback_timeout is not None:
                logger.info(
                    "run_capability_classifier fallback_model model=%s first_error=%s",
                    models[model_index + 1],
                    _error_type(exc),
                )
                model_index += 1
                completion_kwargs = _completion_kwargs(models[model_index], limits, fallback_timeout)
                attempt_messages = model_messages
                continue
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
        if route is not None:
            _record_result(
                "model",
                route.package_id,
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
            )
            return route
        retry_timeout = _retry_timeout(started_at, limits)
        if attempt + 1 >= _MAX_MODEL_ATTEMPTS or content is None or retry_timeout is None:
            return _fail_closed(
                error_type or "invalid_response",
                started_at,
                result_callback=result_callback,
                observation_gate=deadline_gate,
            )
        # 输出不合契约：把结构化错误连同原输出回传，让模型修正后再答一次。
        logger.info("run_capability_classifier repair_retry first_error=%s", error_type)
        completion_kwargs["timeout"] = retry_timeout
        attempt_messages = [
            *model_messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": feedback},
        ]
    raise AssertionError("unreachable")


def _build_messages(
    message: str,
    available_tools: list[str],
    conversation_messages: list[object] | None,
    *,
    mcp_tool_catalog: tuple[McpRouteTool, ...] = (),
    token_counter_fn: Callable[..., int] | None = None,
    limits: _ClassifierLimits | None = None,
    deadline_event: threading.Event | None = None,
    deadline_gate: ClassifierDeadlineGate | None = None,
) -> list[dict[str, str]] | None:
    effective_limits = limits or _effective_classifier_limits()
    if effective_limits is None:
        return None
    current_message = str(message)
    history = _most_recent_complete_turn(conversation_messages, context_turns=effective_limits.context_turns)
    labels = {entry.alias: entry.label for entry in mcp_tool_catalog}
    authorized_mcp_tools = [
        {"alias": name, "service_tool": labels[name]} if name in labels else {"alias": name}
        for name in sorted({name for name in available_tools if is_authorized_mcp_tool_alias(name)})
    ]
    system_message = {
        "role": "system",
        "content": (
            current_prompt_snapshot().classifier_prompt if current_prompt_snapshot() is not None else _system_prompt()
        )
        + "\nAuthorized MCP tools for this request: "
        + json.dumps(authorized_mcp_tools, ensure_ascii=False, separators=(",", ":")),
    }
    messages = [system_message, *history, {"role": "user", "content": current_message}]
    if not _can_begin_blocking_work(deadline_event, deadline_gate):
        return None
    if _within_input_budget(messages, token_counter_fn, effective_limits.max_input_tokens):
        return messages
    if history:
        messages = [system_message, {"role": "user", "content": current_message}]
        if not _can_begin_blocking_work(deadline_event, deadline_gate):
            return None
        if _within_input_budget(messages, token_counter_fn, effective_limits.max_input_tokens):
            return messages
    return _truncate_current_message(
        system_message,
        current_message,
        token_counter_fn,
        effective_limits.max_input_tokens,
        deadline_event=deadline_event,
        deadline_gate=deadline_gate,
    )


def _truncate_current_message(
    system_message: dict[str, str],
    current_message: str,
    token_counter_fn: Callable[..., int] | None,
    max_input_tokens: int,
    *,
    deadline_event: threading.Event | None,
    deadline_gate: ClassifierDeadlineGate | None,
) -> list[dict[str, str]] | None:
    """只为分类截断当前消息：保留开头和结尾，回答模型仍拿到完整原文。"""

    if not _can_begin_blocking_work(deadline_event, deadline_gate):
        return None
    system_tokens = _count_tokens([system_message], token_counter_fn)
    message_budget = (
        max_input_tokens
        - system_tokens
        - _count_tokens([{"role": "user", "content": _TRUNCATION_MARKER}], token_counter_fn)
    )
    if message_budget <= 0:
        return None
    full_tokens = max(1, _count_tokens([{"role": "user", "content": current_message}], token_counter_fn))
    # 按 token 比例估算保留的字符数，再逐步收紧直到落入预算。
    keep_chars = int(len(current_message) * message_budget / full_tokens * 0.95)
    for _ in range(_TRUNCATION_MAX_ROUNDS):
        if keep_chars <= 0:
            return None
        head = current_message[: keep_chars // 2]
        tail = current_message[len(current_message) - (keep_chars - keep_chars // 2) :]
        messages = [system_message, {"role": "user", "content": head + _TRUNCATION_MARKER + tail}]
        if not _can_begin_blocking_work(deadline_event, deadline_gate):
            return None
        if _within_input_budget(messages, token_counter_fn, max_input_tokens):
            return messages
        keep_chars = int(keep_chars * 0.8)
    return None


def _system_prompt() -> str:
    return render_runtime_prompt("classifier.system")


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
    route_result_seen = False
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                text_blocks.append(text)
        elif block.get("type") == "route_results" and not route_result_seen:
            summary = _project_route_result_context(block)
            if summary is not None:
                text_blocks.append(summary)
                route_result_seen = True
    return "\n".join(text_blocks) if text_blocks else None


def _project_route_result_context(block: Mapping) -> str | None:
    """只投影最近路线结果里可用于指代消解的安全展示字段。"""

    origin = _project_route_endpoint(block.get("origin"))
    destination = _project_route_endpoint(block.get("destination"))
    routes = block.get("routes")
    if origin is None or destination is None or not isinstance(routes, list):
        return None
    projected_routes = []
    for route in routes[:3]:
        if not isinstance(route, Mapping) or route.get("mode") not in {"driving", "transit", "walking", "bicycling"}:
            continue
        projected = {"mode": route["mode"]}
        for field_name in ("duration_s", "distance_m", "transfers"):
            value = route.get(field_name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                projected[field_name] = value
        projected_routes.append(projected)
    if not projected_routes:
        return None
    return json.dumps(
        {"prior_route_results": {"origin": origin, "destination": destination, "routes": projected_routes}},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _project_route_endpoint(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    label = value.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    city = value.get("city")
    rendered_label = label.strip()[:120]
    if isinstance(city, str) and city.strip():
        return f"{rendered_label}（{city.strip()[:40]}）"
    return rendered_label


class _TokenCountUnavailable(Exception):
    """token 计数不可用；与真实超预算区分，便于观测。"""


def _within_input_budget(
    messages: Sequence[Mapping[str, str]],
    token_counter_fn: Callable[..., int] | None,
    max_input_tokens: int,
) -> bool:
    return _count_tokens(messages, token_counter_fn) <= max_input_tokens


def _count_tokens(
    messages: Sequence[Mapping[str, str]],
    token_counter_fn: Callable[..., int] | None,
) -> int:
    tokenizer_model = _token_counter_model()
    if tokenizer_model is None:
        raise _TokenCountUnavailable("tokenizer_model_missing")
    try:
        token_count = (token_counter_fn or litellm.token_counter)(
            model=tokenizer_model,
            messages=list(messages),
        )
    except Exception as exc:
        raise _TokenCountUnavailable(type(exc).__name__) from exc
    if not isinstance(token_count, int) or isinstance(token_count, bool):
        raise _TokenCountUnavailable("invalid_token_count")
    return token_count


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


_SINGLE_PRODUCT_PACKAGE_BY_TOOL = {
    spec.tools[0]: package_id
    for package_id, spec in CAPABILITY_PACKAGES.items()
    if spec.model_selectable and spec.is_product_package and len(spec.tools) == 1 and not spec.requires_primary_tool
}


def _parse_model_route(
    response: object,
    _available_tools: list[str],
    *,
    mcp_tool_catalog: tuple[McpRouteTool, ...] = (),
    include_current_date: bool,
) -> _CandidateRoute | None:
    parsed = _ModelRouteResponse.model_validate_json(_response_content(response))
    package_id = parsed.package_id
    if package_id not in CAPABILITY_MODEL_PACKAGE_IDS:
        return None
    spec = CAPABILITY_PACKAGES[package_id]
    explicit_tools = tuple(parsed.explicit_tool_names)
    required_primary_tool_name = parsed.required_primary_tool_name
    dropped_recovery_tools = False
    if spec.has_external_tools and not set(spec.tools).intersection(CAPABILITY_RECOVERY_TOOL_NAMES):
        # 兜底网页工具由服务端自动附加，模型把它们列进产品包或 MCP 包只是冗余，不是冲突。
        product_tools = tuple(name for name in explicit_tools if name not in CAPABILITY_RECOVERY_TOOL_NAMES)
        dropped_recovery_tools = product_tools != explicit_tools
        explicit_tools = product_tools
        if required_primary_tool_name in CAPABILITY_RECOVERY_TOOL_NAMES:
            required_primary_tool_name = None
    if package_id == "mixed_itinerary" and dropped_recovery_tools and len(explicit_tools) == 1:
        # 模型把网页兜底当成了组合的一员；去掉后只剩一个产品工具，按注册表归到其单产品包。
        single_package_id = _SINGLE_PRODUCT_PACKAGE_BY_TOOL.get(explicit_tools[0])
        if single_package_id is not None and required_primary_tool_name in (None, explicit_tools[0]):
            package_id = single_package_id
            spec = CAPABILITY_PACKAGES[package_id]
            required_primary_tool_name = None
    denied_tools = tuple(parsed.denied_tool_names)
    if len(set(denied_tools)) != len(denied_tools):
        return None
    allowed_denials = frozenset(CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER) | frozenset(
        name for name in _available_tools if is_authorized_mcp_tool_alias(name)
    )
    if not set(denied_tools).issubset(allowed_denials):
        return None
    if spec.mcp_aliases:
        if (
            len(explicit_tools) != 1
            or not is_authorized_mcp_tool_alias(explicit_tools[0])
            or explicit_tools[0] not in _available_tools
        ):
            return None
    elif package_id == "mixed_itinerary":
        if (
            not 2 <= len(explicit_tools) <= 3
            or len(set(explicit_tools)) != len(explicit_tools)
            or not set(explicit_tools).issubset(spec.tools)
            or set(explicit_tools) == {"search_flights", "search_trains"}
        ):
            return None
    elif explicit_tools != spec.tools:
        return None
    if spec.requires_primary_tool:
        if required_primary_tool_name not in explicit_tools:
            return None
    elif required_primary_tool_name is not None:
        return None
    canonical_tools = (
        _same_service_aliases(explicit_tools[0], _available_tools, mcp_tool_catalog)
        if spec.mcp_aliases
        else tuple(name for name in CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER if name in explicit_tools)
    )
    return _CandidateRoute(
        package_id=package_id,
        confidence=spec.confidence_options[0],
        reason_codes=spec.reason_code_options[0],
        include_current_date=spec.route_include_current_date(include_current_date),
        resolution_mode=spec.resolution_mode,
        explicit_tool_names=canonical_tools or None,
        network_policy=parsed.network_policy,
        denied_tool_names=denied_tools,
        required_primary_tool_name=required_primary_tool_name,
    )


def _same_service_aliases(
    chosen: str,
    available_tools: list[str],
    mcp_tool_catalog: tuple[McpRouteTool, ...],
) -> tuple[str, ...]:
    """同一服务的授权工具一起公告，所选工具排在最前。"""

    service_id = next((entry.service_id for entry in mcp_tool_catalog if entry.alias == chosen), None)
    if service_id is None:
        return (chosen,)
    siblings = [
        entry.alias
        for entry in mcp_tool_catalog
        if entry.service_id == service_id and entry.alias != chosen and entry.alias in available_tools
    ]
    return (chosen, *siblings)[:CAPABILITY_MAX_MCP_ALIASES]


def _classifier_models() -> list[str]:
    primary = settings.RUN_CAPABILITY_CLASSIFIER_MODEL
    fallback = (settings.RUN_CAPABILITY_CLASSIFIER_FALLBACK_MODEL or "").strip()
    if not fallback or fallback == primary or fallback.startswith("litellm_proxy/"):
        return [primary]
    return [primary, fallback]


def _completion_kwargs(model: str, limits: _ClassifierLimits, timeout: float) -> dict:
    return {
        "model": f"litellm_proxy/{model}",
        "timeout": timeout,
        "num_retries": 0,
        "max_tokens": limits.max_output_tokens,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        **merge_litellm_kwargs(
            "run_capability_classifier",
            {
                "api_key": settings.LITELLM_API_KEY,
                "api_base": settings.LITELLM_PROXY_URL,
                **deepcopy(_NON_REASONING_KWARGS_BY_MODEL.get(model, _DEFAULT_NON_REASONING_KWARGS)),
            },
        ),
    }


def _retry_timeout(started_at: float, limits: _ClassifierLimits) -> float | None:
    remaining = CLASSIFIER_TOTAL_DEADLINE_SECONDS - (perf_counter() - started_at) - _REPAIR_RETRY_MARGIN_SECONDS
    if remaining < _REPAIR_RETRY_MIN_SECONDS:
        return None
    return min(limits.timeout_seconds, remaining)


def _validation_feedback(error: ValidationError) -> str:
    problems = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(root)'}: {item['msg']}"
        for item in error.errors(include_url=False)[:5]
    )
    return f"上一次输出没有通过 JSON 校验：{problems}。请修正后按系统说明只输出一个 JSON 对象。"


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
    fallback = classifier_unavailable_route()
    _record_result(
        "failed",
        fallback.package_id,
        started_at,
        error_type=error_type,
        result_callback=result_callback,
        observation_gate=observation_gate,
    )
    return fallback


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
    return classifier_unavailable_route()


def _deadline_expired(
    deadline_event: threading.Event | None,
    deadline_gate: ClassifierDeadlineGate | None,
) -> bool:
    return (deadline_event is not None and deadline_event.is_set()) or (
        deadline_gate is not None and deadline_gate.is_expired()
    )


def _can_begin_blocking_work(
    deadline_event: threading.Event | None,
    deadline_gate: ClassifierDeadlineGate | None,
) -> bool:
    """deadline 已赢时，不再开始 token 计数等可能阻塞的预处理。"""

    if deadline_event is not None and deadline_event.is_set():
        return False
    return deadline_gate is None or deadline_gate.try_begin_blocking_work()


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
