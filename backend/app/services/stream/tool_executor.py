"""并行工具执行 + emitter→Redis 适配器。

spec §4.3。execute_tools_parallel 走 emitter 的
tool_call_started/completed 协议，并把 execute_tool_with_retry
（瞬时重试 + 30s timeout）夹在 start/completed 之间。
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import backoff

from app.core.logger import app_logger as logger
from app.core.metrics import observe_tool_call
from app.services.agent.emitter import AgentEventEmitter
from app.services.agent.progress_digest import build_evidence_items, build_tool_result_digest
from app.services.agent.trajectory_payload import is_cancellation_terminal_event
from app.services.mcp.tool_contract import (
    max_tool_argument_json_bytes,
    validate_tool_argument_resource_limits,
)
from app.services.stream.tool_call_lifecycle import (
    ToolLifecycleControlPlaneError,
    emit_tool_call_result,
    emit_tool_call_started,
    execute_tool_with_lifecycle,
)
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream_state_service import (
    StreamOwnershipLostError,
    StreamWriteTerminalError,
    StreamWriteUnavailableError,
    append_chunk,
)

if TYPE_CHECKING:
    from app.services.stream.network_budget import NetworkToolBudget
    from app.services.tool_handlers.base import ToolRuntimeContext

# 单次工具调用超时
AGENT_TOOL_TIMEOUT = 30
# 瞬时故障重试次数
AGENT_TOOL_MAX_RETRIES = 1
# 权威计划快照沿用 Stream 的连续失败阈值，但在返回前必须取得写入确认。
AGENT_EVENT_AUTHORITATIVE_WRITE_ATTEMPTS = 3

_RETRYABLE_TOOL_ERROR_CODES = frozenset(
    {
        "search_unavailable",
        "tool_timeout",
        "upstream_timeout",
        "upstream_unavailable",
    }
)
_INTERNAL_TOOL_ARG_KEYS = {"budget_decision"}
_AMAP_PRODUCT_EXECUTION_PRIORITY = {
    "route_compare": 0,
    "local_place_search": 1,
    "weather_forecast": 2,
}


@dataclass(frozen=True)
class ToolExecutionBatchRequest:
    conversation_id: str
    user_id: str
    model_id: str
    provider: str
    trace_id: str | None = None
    step_number: int | None = None
    message_id: str | None = None
    emitter: Optional[AgentEventEmitter] = None
    network_budget: "NetworkToolBudget | None" = None
    tool_handlers: Mapping[str, Any] | None = None
    runtime_context: "ToolRuntimeContext | None" = None
    successful_tool_call_signatures: set[str] | None = None


@dataclass(frozen=True)
class ToolExecutionIds:
    block_id: str
    log_id: str


@dataclass
class ToolAttemptLifecycle:
    """同一 logical tool call 下的物理 handler attempt 计数器。"""

    emitter: AgentEventEmitter
    tool_call_id: str
    tool_name: str
    attempt_index: int = 0

    async def execute(self, operation):
        self.attempt_index += 1
        tool_attempt_id = str(uuid.uuid4())
        try:
            await self.emitter.tool_attempt_started(
                tool_attempt_id=tool_attempt_id,
                tool_call_id=self.tool_call_id,
                tool_name=self.tool_name,
                attempt_index=self.attempt_index,
            )
        except BaseException as error:
            raise ToolLifecycleControlPlaneError(error) from error
        started_at = time.monotonic()
        try:
            result = await operation()
        except asyncio.CancelledError:
            await self._complete_preserving_primary(
                tool_attempt_id=tool_attempt_id,
                status="cancelled",
                error_code=None,
                started_at=started_at,
            )
            raise
        except BaseException:
            await self._complete_preserving_primary(
                tool_attempt_id=tool_attempt_id,
                status="failed",
                error_code=None,
                started_at=started_at,
            )
            raise

        data = result.data if isinstance(result.data, dict) else {}
        attempt_error_code = _attempt_error_code(data)
        if attempt_error_code == "tool_timeout":
            status = "timeout"
            error_code = "tool_timeout"
        elif result.status in {"success", "degraded"}:
            # degraded 目前仍记为 success：把它改成独立状态会翻转既有 span 语义
            # （trajectory_projector 与前端面板都读这个字段），属于另案决定。
            status = "success"
            error_code = None
        else:
            status = "failed"
            # 工具通常已经给出错误码（MCP 侧有完整词汇表），此前一律写死 None，
            # 排查工具失败时只能回头 join tool_call_logs 才拿得到原因。
            error_code = attempt_error_code
        await self._complete(
            tool_attempt_id=tool_attempt_id,
            status=status,
            error_code=error_code,
            started_at=started_at,
        )
        return result

    async def _complete_preserving_primary(self, **kwargs: Any) -> None:
        try:
            await self._complete(**kwargs)
        except ToolLifecycleControlPlaneError as secondary:
            logger.warning(
                "工具 attempt 生命周期收尾失败，保留主异常: error_type=%s",
                type(secondary.error).__name__,
            )

    async def _complete(
        self,
        *,
        tool_attempt_id: str,
        status: str,
        error_code: str | None,
        started_at: float,
    ) -> None:
        try:
            await self.emitter.tool_attempt_completed(
                tool_attempt_id=tool_attempt_id,
                tool_call_id=self.tool_call_id,
                status=status,
                error_code=error_code,
                duration_ms=max(0, int(round((time.monotonic() - started_at) * 1000))),
            )
        except BaseException as error:
            raise ToolLifecycleControlPlaneError(error) from error


def _attempt_error_code(data: dict) -> str | None:
    """只接受非空字符串错误码，其余一律视为缺失。

    取值来自工具自身的结果载荷，不是上游原文：MCP 侧是一组固定内部码，
    上游厂商的原始报错另存于 tool_call_logs 的 error_details。
    """
    value = data.get("error_code")
    return value if isinstance(value, str) and value else None


def _should_retry_tool_result(result) -> bool:
    """决定 ToolResult 是否应该再试一次（True = 重试，False = 接受当前结果）。"""
    if result.status in ("success", "degraded"):
        return False
    data = result.data if isinstance(result.data, dict) else {}
    if isinstance(data.get("retryable"), bool):
        return data["retryable"]
    error_code = data.get("error_code")
    return isinstance(error_code, str) and error_code in _RETRYABLE_TOOL_ERROR_CODES


def _log_tool_retry(details: dict[str, Any]) -> None:
    handler = details["args"][0]
    result = details.get("value")
    data = getattr(result, "data", None)
    error_code = data.get("error_code") if isinstance(data, dict) else None
    logger.warning(
        "工具执行失败后重试: tool=%s attempt=%s wait_seconds=%s error_code=%s",
        getattr(handler, "tool_name", ""),
        details.get("tries"),
        int(details.get("wait", 0)),
        error_code if isinstance(error_code, str) else "unknown",
    )


class AgentEventRedisWriter:
    """把 emitter 的 (conv_id, chunk_type, payload:dict) 调用转成
    stream_state_service.append_chunk(conv_id, chunk_type, content, block_id, task_id=task_id)。

    Task 9 引入的 adapter — emitter 不直接知道 stream_state_service 的接口形态，
    通过本 adapter 桥接：payload JSON 序列化进 content 字段，block_id 留空。
    """

    async def append_chunk(self, conversation_id: str, task_id: str, chunk_type: str, payload: dict) -> None:
        is_authoritative_plan_snapshot = payload.get("type") == "plan_snapshot"
        attempts = AGENT_EVENT_AUTHORITATIVE_WRITE_ATTEMPTS if is_authoritative_plan_snapshot else 1
        for _ in range(attempts):
            entry_id = await append_chunk(
                conversation_id,
                chunk_type,
                json.dumps(payload, ensure_ascii=False),
                "",  # block_id 不适用于 agent_event chunk
                task_id=task_id,
            )
            if entry_id is not None:
                return
        if is_authoritative_plan_snapshot:
            raise StreamWriteUnavailableError("权威计划快照未写入 Redis，已终止本次生成")


class AgentEventCompositeWriter:
    """agent_event 多写 adapter：Redis 必需，两个旁路 sink 独立 fail-open。"""

    def __init__(self, *, redis_writer: AgentEventRedisWriter, recorder=None, trajectory_recorder=None) -> None:
        self.redis_writer = redis_writer
        self.recorder = recorder
        self.trajectory_recorder = trajectory_recorder

    async def append_chunk(self, conversation_id: str, task_id: str, chunk_type: str, payload: dict) -> None:
        try:
            await self.redis_writer.append_chunk(conversation_id, task_id, chunk_type, payload)
        except StreamOwnershipLostError:
            # stop 先冻结实时流；仅保留真实取消事实到该 Run 账本，不能恢复写入权或更新 progress。
            if (
                chunk_type == "agent_event"
                and is_cancellation_terminal_event(payload)
                and self.trajectory_recorder is not None
            ):
                try:
                    await self.trajectory_recorder.record_chunk(conversation_id, chunk_type, payload)
                except BaseException as error:  # 辅助记录失败不得替换原所有权异常或取消流程。
                    logger.warning(
                        "中断终态旁路记录失败: run_id=%s error_type=%s",
                        payload.get("run_id"),
                        type(error).__name__,
                    )
            raise
        if self.recorder is not None:
            try:
                self.recorder.record_chunk(conversation_id, chunk_type, payload)
            except Exception as error:  # noqa: BLE001 — progress 是辅助 sink
                logger.warning(
                    "Agent progress 旁路记录失败: run_id=%s error_type=%s",
                    payload.get("run_id"),
                    type(error).__name__,
                )
        if self.trajectory_recorder is not None:
            try:
                await self.trajectory_recorder.record_chunk(conversation_id, chunk_type, payload)
            except Exception as error:  # noqa: BLE001 — trajectory 是辅助 sink
                logger.warning(
                    "Trajectory 旁路记录失败: run_id=%s error_type=%s",
                    payload.get("run_id"),
                    type(error).__name__,
                )


@backoff.on_predicate(
    backoff.constant,
    predicate=_should_retry_tool_result,
    max_tries=AGENT_TOOL_MAX_RETRIES + 1,
    interval=1,
    on_backoff=_log_tool_retry,
)
async def execute_tool_with_retry(
    handler,
    args: dict,
    runtime_context: "ToolRuntimeContext | None" = None,
    attempt_lifecycle: ToolAttemptLifecycle | None = None,
):
    """带重试的工具执行（仅瞬时故障重试），返回 ToolResult。

    永久性错误（not_found / invalid / 401 / 403 / 404 / 400 / rate_limit）不重试。
    超时：单次 AGENT_TOOL_TIMEOUT 秒，超时被视为可重试失败。
    重试逻辑由 @backoff.on_predicate 装饰器实现。
    """
    from app.services.tool_handlers import ToolResult

    async def execute_attempt():
        try:
            return await asyncio.wait_for(
                handler.execute(args, runtime_context),
                timeout=_handler_timeout_seconds(handler),
            )
        except asyncio.TimeoutError:
            return ToolResult(
                status="failed",
                error_message="工具调用超时",
                data={"error_code": "tool_timeout", "retryable": True},
            )

    if attempt_lifecycle is None:
        return await execute_attempt()
    return await attempt_lifecycle.execute(execute_attempt)


async def execute_tool_once(
    handler,
    args: dict,
    runtime_context: "ToolRuntimeContext | None" = None,
    attempt_lifecycle: ToolAttemptLifecycle | None = None,
):
    """有副作用或不具备幂等保证的工具只执行一次，但仍保留统一超时。"""
    from app.services.tool_handlers import ToolResult

    async def execute_attempt():
        try:
            return await asyncio.wait_for(
                handler.execute(args, runtime_context),
                timeout=_handler_timeout_seconds(handler),
            )
        except asyncio.TimeoutError:
            return ToolResult(
                status="failed",
                error_message="工具调用超时",
                data={"error_code": "tool_timeout", "retryable": False},
            )

    if attempt_lifecycle is None:
        return await execute_attempt()
    return await attempt_lifecycle.execute(execute_attempt)


def _handler_timeout_seconds(handler: Any) -> float:
    """已知慢工具（如生图）可声明更长的单次超时，其余沿用统一上限。"""
    declared = handler.execution_timeout_seconds
    if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared > AGENT_TOOL_TIMEOUT:
        return float(declared)
    return AGENT_TOOL_TIMEOUT


def new_tool_execution_ids() -> ToolExecutionIds:
    return ToolExecutionIds(
        block_id=f"blk_{uuid.uuid4().hex[:12]}",
        log_id=str(uuid.uuid4()),
    )


def resolve_tool_handler(tool_name: str, tool_handlers: Mapping[str, Any] | None = None):
    if tool_handlers and tool_name in tool_handlers:
        return tool_handlers[tool_name]
    from app.services.tool_handlers import get_handler as _get_handler

    return _get_handler(tool_name)


def parse_tool_arguments(tool_call: dict) -> dict:
    arguments = tool_call.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        raise ToolArgumentsParseError
    if len(arguments.encode("utf-8")) > max_tool_argument_json_bytes(tool_call.get("name")):
        raise ToolArgumentsLimitError("max_bytes")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        raise ToolArgumentsParseError from None
    if not isinstance(parsed, dict):
        raise ToolArgumentsParseError
    return parsed


class ToolArgumentsParseError(ValueError):
    pass


class ToolArgumentsLimitError(ToolArgumentsParseError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


ARGUMENT_ERROR_MESSAGE = "模型给出的工具参数不符合要求，工具未执行"
_ARGUMENT_FIELD_PATTERN = re.compile(
    r"^(?:\$|[A-Za-z_][A-Za-z0-9_:-]{0,63})"
    r"(?:(?:\[\])|(?:\.\*)|(?:\.[A-Za-z_][A-Za-z0-9_:-]{0,63}))*$"
)


def build_successful_call_signature(
    tool_call: dict,
    tool_handlers: Mapping[str, Any] | None = None,
) -> str | None:
    """返回 handler 显式声明的运行内成功调用签名。"""

    handler = resolve_tool_handler(str(tool_call.get("name", "")), tool_handlers)
    if handler is None:
        return None
    try:
        args = parse_tool_arguments(tool_call)
    except ToolArgumentsParseError:
        return None
    if not isinstance(args, dict):
        return None
    try:
        signature = handler.build_successful_call_signature(args)
    except Exception as error:  # noqa: BLE001 — 去重签名失败必须回退真实执行
        logger.warning(
            "工具成功调用签名生成失败: tool=%s error_type=%s",
            getattr(handler, "tool_name", ""),
            type(error).__name__,
        )
        return None
    return signature if isinstance(signature, str) and signature else None


def prepare_tool_arguments(
    *,
    tool_name: str,
    args: dict,
    network_budget: "NetworkToolBudget | None",
):
    if network_budget is None:
        return args, None
    if tool_name == "web_search":
        return network_budget.prepare_web_search_args(args)
    if tool_name == "url_read":
        return network_budget.prepare_url_read_args(args)
    return args, None


def strip_internal_tool_arguments(args: dict) -> dict:
    if not any(key in args for key in _INTERNAL_TOOL_ARG_KEYS):
        return args
    return {key: value for key, value in args.items() if key not in _INTERNAL_TOOL_ARG_KEYS}


def attach_internal_tool_metadata(result, args: dict) -> None:
    budget_decision = args.get("budget_decision")
    if isinstance(budget_decision, dict):
        result.data.setdefault("budget_decision", budget_decision)


def build_tool_execution_record(
    *,
    tool_call: dict,
    result,
    handler,
    ids: ToolExecutionIds,
    reused: bool = False,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        tool_call=tool_call,
        result=result,
        handler=handler,
        block_id=ids.block_id,
        log_id=ids.log_id,
        reused=reused,
    )


def build_unknown_tool_record(*, tool_call: dict, ids: ToolExecutionIds) -> ToolExecutionRecord:
    from app.services.tool_handlers import ToolResult

    logger.warning(f"未知的 tool_call: {tool_call['name']}")
    return build_tool_execution_record(
        tool_call=tool_call,
        result=ToolResult(status="failed", error_message=f"未知工具: {tool_call['name']}"),
        handler=None,
        ids=ids,
    )


def build_reused_tool_record(
    *,
    tool_call: dict,
    handler,
    ids: ToolExecutionIds,
) -> ToolExecutionRecord:
    from app.services.tool_handlers import ToolResult

    return build_tool_execution_record(
        tool_call=tool_call,
        result=ToolResult(
            status="success",
            data={"reused_successful_result": True},
            duration_ms=0,
        ),
        handler=handler,
        ids=ids,
        reused=True,
    )


async def emit_budget_result(
    *,
    request: ToolExecutionBatchRequest,
    tool_call: dict,
    handler,
    args: dict,
    result,
) -> None:
    await emit_tool_call_started(
        request.emitter,
        tool_call_id=tool_call["id"],
        tool_name=handler.tool_name,
        arguments=_sanitize_event_arguments(handler, args),
        plan_item_id=tool_call.get("plan_item_id"),
    )
    await emit_tool_call_result(
        request.emitter,
        tool_call_id=tool_call["id"],
        tool_name=handler.tool_name,
        result=result,
        duration_ms=result.duration_ms or 0,
        result_summary_builder=handler._build_result_summary,
        plan_item_id=tool_call.get("plan_item_id"),
    )


async def execute_tool_handler(*, request: ToolExecutionBatchRequest, tool_call: dict, handler, args: dict):
    executor = execute_tool_once if handler.supports_automatic_retry is False else execute_tool_with_retry
    if request.emitter is None:
        return await executor(handler, args, request.runtime_context)
    attempt_lifecycle = ToolAttemptLifecycle(
        emitter=request.emitter,
        tool_call_id=tool_call["id"],
        tool_name=handler.tool_name,
    )
    return await execute_tool_with_lifecycle(
        tool_call_id=tool_call["id"],
        tool_name=handler.tool_name,
        args=_sanitize_event_arguments(handler, args),
        target=handler,
        execute=lambda target, _event_arguments: executor(
            target,
            args,
            request.runtime_context,
            attempt_lifecycle,
        ),
        result_summary_builder=handler._build_result_summary,
        emitter=request.emitter,
        plan_item_id=tool_call.get("plan_item_id"),
    )


def _sanitize_event_arguments(handler, args: dict) -> dict:
    return handler.sanitize_input_params_for_event(args)


async def log_tool_execution(
    *,
    request: ToolExecutionBatchRequest,
    handler,
    ids: ToolExecutionIds,
    result,
    args: dict,
    tool_call_id: str | None,
) -> None:
    await handler.log(
        log_id=ids.log_id,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
        model_id=request.model_id,
        provider=request.provider,
        result=result,
        input_params=args,
        trace_id=request.trace_id,
        tool_call_id=tool_call_id,
        step_number=request.step_number,
        message_id=request.message_id,
    )


async def emit_progress_digest_events(
    *,
    request: ToolExecutionBatchRequest,
    record: ToolExecutionRecord,
) -> None:
    if request.emitter is None:
        return
    try:
        digest = build_tool_result_digest(record)
        plan_item_id = record.tool_call.get("plan_item_id")
        if isinstance(plan_item_id, str):
            digest["plan_item_id"] = plan_item_id
        await request.emitter.tool_result_digest(**digest)
        for evidence in build_evidence_items(record):
            await request.emitter.evidence_item_upserted(
                tool_call_id=str(record.tool_call.get("id", "")),
                evidence=evidence,
            )
    except StreamWriteTerminalError:
        raise
    except Exception as error:  # noqa: BLE001 — v2 非关键进度事件失败不能中断工具结果主链路
        logger.warning(
            "工具 digest 事件发送失败: tool=%s error_type=%s",
            record.tool_name,
            type(error).__name__,
        )


async def execute_one_tool_call(request: ToolExecutionBatchRequest, tool_call: dict) -> ToolExecutionRecord:
    handler = resolve_tool_handler(tool_call["name"], request.tool_handlers)
    ids = new_tool_execution_ids()
    if not handler:
        return build_unknown_tool_record(tool_call=tool_call, ids=ids)

    successful_signature = build_successful_call_signature(tool_call, request.tool_handlers)
    if (
        successful_signature is not None
        and request.successful_tool_call_signatures is not None
        and successful_signature in request.successful_tool_call_signatures
    ):
        return build_reused_tool_record(tool_call=tool_call, handler=handler, ids=ids)

    try:
        args = parse_tool_arguments(tool_call)
    except ToolArgumentsLimitError as error:
        args = {}
        result = _build_argument_error_result(
            error_code="arguments_schema_invalid",
            validation_errors=[f"$:{error.code}"],
        )
        return await _complete_preflight_tool_result(
            request=request,
            tool_call=tool_call,
            handler=handler,
            ids=ids,
            args=args,
            result=result,
        )
    except ToolArgumentsParseError:
        args = {}
        result = _build_argument_error_result(
            error_code="arguments_json_invalid",
            validation_errors=["$:invalid_json"],
        )
        return await _complete_preflight_tool_result(
            request=request,
            tool_call=tool_call,
            handler=handler,
            ids=ids,
            args=args,
            result=result,
        )
    validation_errors = _validate_handler_arguments(handler, args)
    if validation_errors:
        result = _build_handler_local_preflight_result(handler, validation_errors)
        if result is None:
            result = _build_argument_error_result(
                error_code="arguments_schema_invalid",
                validation_errors=validation_errors,
            )
        return await _complete_preflight_tool_result(
            request=request,
            tool_call=tool_call,
            handler=handler,
            ids=ids,
            args={},
            result=result,
        )
    runtime_argument_error = _build_runtime_argument_error_result(request, handler, args)
    if runtime_argument_error is not None:
        return await _complete_preflight_tool_result(
            request=request,
            tool_call=tool_call,
            handler=handler,
            ids=ids,
            args={},
            result=runtime_argument_error,
        )
    args, budget_result = prepare_tool_arguments(
        tool_name=tool_call["name"],
        args=args,
        network_budget=request.network_budget,
    )
    executable_args = strip_internal_tool_arguments(args)
    if budget_result is not None:
        result = budget_result
        await emit_budget_result(
            request=request,
            tool_call=tool_call,
            handler=handler,
            args=executable_args,
            result=result,
        )
    else:
        result = await execute_tool_handler(
            request=request,
            tool_call=tool_call,
            handler=handler,
            args=executable_args,
        )
    result = _normalize_remote_argument_failure(result)
    attach_internal_tool_metadata(result, args)
    if (
        result.status == "success"
        and successful_signature is not None
        and request.successful_tool_call_signatures is not None
    ):
        request.successful_tool_call_signatures.add(successful_signature)

    await log_tool_execution(
        request=request,
        handler=handler,
        ids=ids,
        result=result,
        args=executable_args,
        tool_call_id=tool_call["id"],
    )
    record = build_tool_execution_record(tool_call=tool_call, result=result, handler=handler, ids=ids)
    await emit_progress_digest_events(request=request, record=record)
    return record


async def _complete_preflight_tool_result(
    *,
    request: ToolExecutionBatchRequest,
    tool_call: dict,
    handler,
    ids: ToolExecutionIds,
    args: dict,
    result,
) -> ToolExecutionRecord:
    await emit_budget_result(
        request=request,
        tool_call=tool_call,
        handler=handler,
        args=args,
        result=result,
    )
    await log_tool_execution(
        request=request,
        handler=handler,
        ids=ids,
        result=result,
        args=args,
        tool_call_id=tool_call["id"],
    )
    record = build_tool_execution_record(
        tool_call=tool_call,
        result=result,
        handler=handler,
        ids=ids,
    )
    await emit_progress_digest_events(request=request, record=record)
    return record


def _validate_handler_arguments(
    handler: Any,
    args: dict,
) -> list[str]:
    raw_errors = validate_tool_argument_resource_limits(
        args,
        max_json_bytes=max_tool_argument_json_bytes(getattr(handler, "tool_name", None)),
    )
    try:
        if not raw_errors:
            raw_errors = handler.validate_arguments(args)
    except Exception as error:  # noqa: BLE001 — 本地预校验异常必须回退为不可执行，不能触网
        logger.warning(
            "工具参数本地预校验异常: tool=%s error_type=%s",
            getattr(handler, "tool_name", ""),
            type(error).__name__,
        )
        return ["request:validator_error"]
    if not isinstance(raw_errors, list):
        return []
    errors: list[str] = []
    for item in raw_errors:
        if isinstance(item, dict):
            field = item.get("field")
            error_type = item.get("code")
            separator = ":" if isinstance(field, str) and isinstance(error_type, str) else ""
        elif isinstance(item, str) and len(item) <= 128:
            field, separator, error_type = item.partition(":")
        else:
            continue
        if (
            separator
            and isinstance(field, str)
            and isinstance(error_type, str)
            and _ARGUMENT_FIELD_PATTERN.fullmatch(field)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_type)
        ):
            errors.append(f"{field}:{error_type}")
    return list(dict.fromkeys(errors))[:8]


def _build_handler_local_preflight_result(
    handler: Any,
    validation_errors: list[str],
):
    """允许 handler 安全投影状态型预检结果；异常时回退通用参数修复。"""

    try:
        result = handler.build_local_preflight_result(list(validation_errors))
    except Exception as error:  # noqa: BLE001 — 不记录可能包含业务数据的异常文本
        logger.warning(
            "工具自定义预检结果构造失败: tool=%s error_type=%s",
            getattr(handler, "tool_name", ""),
            type(error).__name__,
        )
        return None

    from app.services.tool_handlers import ToolResult

    return result if isinstance(result, ToolResult) else None


def _build_argument_error_result(
    *,
    error_code: str,
    validation_errors: list[str] | None = None,
    hints: dict[str, list[str]] | None = None,
    local_preflight: bool = True,
):
    """参数不合法时工具不执行：把具体错误（和已知取值提示）原样交还模型，由模型决定怎么改、要不要再调。"""

    from app.services.tool_handlers import ToolResult

    data: dict[str, Any] = {
        "error_code": error_code,
        "local_preflight": local_preflight,
        "argument_errors": list(validation_errors or [])[:8],
    }
    if hints:
        data["argument_hints"] = hints
    return ToolResult(status="failed", data=data, error_message=ARGUMENT_ERROR_MESSAGE)


def _build_runtime_argument_error_result(
    request: ToolExecutionBatchRequest,
    handler: Any,
    args: dict,
):
    try:
        spec = handler.check_runtime_arguments(args, request.runtime_context)
    except Exception as error:  # noqa: BLE001 — 运行时参数检查异常必须拒绝触网
        logger.warning(
            "工具运行时参数检查异常: tool=%s error_type=%s",
            getattr(handler, "tool_name", ""),
            type(error).__name__,
        )
        return _build_runtime_argument_guard_failure()
    if not isinstance(spec, dict):
        return None
    error_code = spec.get("error_code")
    validation_errors = spec.get("validation_errors")
    if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
        return _build_runtime_argument_guard_failure()
    if not isinstance(validation_errors, list):
        return _build_runtime_argument_guard_failure()
    safe_errors = [
        item
        for item in validation_errors[:8]
        if isinstance(item, str)
        and (parts := item.partition(":"))[1]
        and _ARGUMENT_FIELD_PATTERN.fullmatch(parts[0])
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", parts[2])
    ]
    if not safe_errors:
        return _build_runtime_argument_guard_failure()
    return _build_argument_error_result(
        error_code=error_code,
        validation_errors=safe_errors,
        hints=_safe_argument_hints(spec.get("hints")),
    )


def _safe_argument_hints(raw_hints: Any) -> dict[str, list[str]]:
    """提示值来自前序工具结果（第三方数据），只保留短字符串。"""

    if not isinstance(raw_hints, dict):
        return {}
    hints: dict[str, list[str]] = {}
    for field, raw_values in list(raw_hints.items())[:8]:
        if (
            not isinstance(field, str)
            or not _ARGUMENT_FIELD_PATTERN.fullmatch(field)
            or not isinstance(raw_values, list)
        ):
            continue
        values = [
            value.strip()
            for value in raw_values[:4]
            if isinstance(value, str) and value.strip() and len(value.strip()) <= 120
        ]
        if values:
            hints[field] = list(dict.fromkeys(values))
    return hints


def _build_runtime_argument_guard_failure():
    from app.services.tool_handlers import ToolResult

    return ToolResult(
        status="failed",
        data={
            "error_code": "runtime_argument_guard_failed",
            "local_preflight": True,
        },
        error_message="工具参数检查异常，工具未执行",
    )


def _normalize_remote_argument_failure(result):
    """远端按参数错误拒绝时，与本地校验一样把字段级错误交还模型。"""

    data = getattr(result, "data", None)
    if not isinstance(data, dict) or data.get("error_code") != "remote_invalid_arguments":
        return result
    validation_errors = [item for item in data.get("validation_errors", []) if isinstance(item, str)][:8]
    if not validation_errors:
        return result
    argument_result = _build_argument_error_result(
        error_code="remote_arguments_invalid",
        validation_errors=validation_errors,
        local_preflight=False,
    )
    argument_result.duration_ms = result.duration_ms
    return argument_result


async def execute_tool_batch(
    request: ToolExecutionBatchRequest,
    tool_calls: list[dict],
) -> list[ToolExecutionRecord]:
    indexed_calls = list(enumerate(tool_calls))
    amap_product_calls = [
        (index, tool_call)
        for index, tool_call in indexed_calls
        if tool_call.get("name") in _AMAP_PRODUCT_EXECUTION_PRIORITY
    ]
    parallel_calls = [
        (index, tool_call)
        for index, tool_call in indexed_calls
        if tool_call.get("name") not in _AMAP_PRODUCT_EXECUTION_PRIORITY
    ]
    reusable_groups: dict[str, list[tuple[int, dict]]] = {}
    ungrouped_parallel_calls: list[tuple[int, dict]] = []
    for index, tool_call in parallel_calls:
        signature = (
            build_successful_call_signature(tool_call, request.tool_handlers)
            if request.successful_tool_call_signatures is not None
            else None
        )
        if signature is None:
            ungrouped_parallel_calls.append((index, tool_call))
        else:
            reusable_groups.setdefault(signature, []).append((index, tool_call))

    async def execute_indexed(index: int, tool_call: dict) -> tuple[int, ToolExecutionRecord]:
        record = await execute_one_tool_call(request, tool_call)
        observe_tool_call(
            tool_name=record.tool_name,
            known=record.handler is not None,
            status=record.result.status,
            reused=record.reused,
            duration_ms=record.result.duration_ms,
        )
        return index, record

    async def execute_amap_products() -> list[tuple[int, ToolExecutionRecord]]:
        records = []
        for index, tool_call in sorted(
            amap_product_calls,
            key=lambda item: (_AMAP_PRODUCT_EXECUTION_PRIORITY[item[1]["name"]], item[0]),
        ):
            records.append(await execute_indexed(index, tool_call))
        return records

    async def execute_reusable_group(
        grouped_calls: list[tuple[int, dict]],
    ) -> list[tuple[int, ToolExecutionRecord]]:
        records = []
        for index, tool_call in sorted(grouped_calls, key=lambda item: item[0]):
            records.append(await execute_indexed(index, tool_call))
        return records

    pending = [execute_indexed(index, tool_call) for index, tool_call in ungrouped_parallel_calls]
    pending.extend(execute_reusable_group(grouped_calls) for grouped_calls in reusable_groups.values())
    if amap_product_calls:
        pending.append(execute_amap_products())
    tasks = [asyncio.create_task(operation) for operation in pending]
    batch = asyncio.gather(*tasks)
    try:
        # 父取消立即转入显式清理，避免后续取消经 gather 再次打断工具的 finally。
        batches = await asyncio.shield(batch)
    except BaseException:
        # gather 不会因单个子任务失败而取消兄弟任务；批次退出前必须等本地清理完成。
        for task in tasks:
            # 已在取消中的任务继续完成原清理，不能再次打断 wait_for 的清理等待。
            if not task.done() and not task.cancelling():
                task.cancel()
        # 原聚合 future 也必须消费，否则 shield 后可能遗留未读取的异常。
        cleanup = asyncio.gather(batch, *tasks, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # 清理期间再次取消也不能遗留工具任务，或替换首个控制流异常。
                continue
        raise

    indexed_results: list[tuple[int, ToolExecutionRecord]] = []
    for batch in batches:
        if isinstance(batch, list):
            indexed_results.extend(batch)
        else:
            indexed_results.append(batch)
    indexed_results.sort(key=lambda item: item[0])
    return [record for _, record in indexed_results]


def is_local_argument_preflight_failure(
    tool_call: dict,
    tool_handlers: Mapping[str, Any] | None = None,
) -> bool:
    handler = resolve_tool_handler(str(tool_call.get("name", "")), tool_handlers)
    if handler is None:
        return False
    try:
        args = parse_tool_arguments(tool_call)
    except ToolArgumentsParseError:
        return True
    return bool(_validate_handler_arguments(handler, args))


async def execute_tools_parallel(
    tool_calls: list[dict],
    conversation_id: str,
    user_id: str,
    model_id: str,
    provider: str,
    trace_id: str = None,
    step_number: int = None,
    message_id: str | None = None,
    emitter: Optional[AgentEventEmitter] = None,
    network_budget: "NetworkToolBudget | None" = None,
    tool_handlers: Mapping[str, Any] | None = None,
    runtime_context: "ToolRuntimeContext | None" = None,
    successful_tool_call_signatures: set[str] | None = None,
) -> list[ToolExecutionRecord]:
    """
    并行执行所有 tool_calls。

    统一走 tool_call 生命周期协议（tool_call_started / completed agent_event），
    但中间塞入 execute_tool_with_retry（瞬时重试 + 30s timeout）。
    tool_call_logs 仍通过 handler.log 写入。

    返回 ToolExecutionRecord 列表，调用方不再依赖裸 tuple 位置。
    """
    request = ToolExecutionBatchRequest(
        conversation_id=conversation_id,
        user_id=user_id,
        model_id=model_id,
        provider=provider,
        trace_id=trace_id,
        step_number=step_number,
        message_id=message_id,
        emitter=emitter,
        network_budget=network_budget,
        tool_handlers=tool_handlers,
        runtime_context=runtime_context,
        successful_tool_call_signatures=successful_tool_call_signatures,
    )
    return await execute_tool_batch(request, tool_calls)
