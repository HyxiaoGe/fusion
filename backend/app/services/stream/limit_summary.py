"""Agent 触顶后的强制总结 step 编排。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Any

from app.ai.llm_round_observability import create_llm_round_observation
from app.ai.prompts.agent_loop import LIMIT_SUMMARY_PROMPT as _LIMIT_SUMMARY_PROMPT
from app.ai.prompts.agent_loop import (
    NO_PROGRESS_SUMMARY_PROMPT,
    NO_TOOL_EVIDENCE_SUMMARY_PROMPT,
    RESEARCH_EVIDENCE_SUMMARY_PROMPT,
    get_limit_summary_prompt,
)
from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages, to_provider_messages
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.ai.prompts.section_ids import (
    DEEP_RESEARCH_CONTROL_SECTION_IDS,
    LIMIT_SUMMARY,
    NO_PROGRESS_SUMMARY,
    RESEARCH_COMPLETION_REPAIR,
    RESEARCH_EVIDENCE_SUMMARY,
    SUMMARY_TOOL_PROTOCOL_RETRY,
    is_terminal_control_section,
)
from app.core.logger import app_logger as logger
from app.schemas.chat import ContextUsage, TextBlock, ThinkingBlock, Usage
from app.services.chat.context_manager import ContextManagementError, ContextPlan, prepare_context
from app.services.chat.model_call_language_policy import finalize_model_call_language_policy
from app.services.chat.tool_transcript import without_tool_transactions
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.context_status import build_context_usage, emit_context_status
from app.services.stream.limit_summary_fact_guard import has_tool_evidence
from app.services.stream.llm_round_lifecycle import (
    LLMRoundLifecycle,
    accumulate_token_usage,
    round_tool_names,
)
from app.services.stream.llm_stream import contains_tool_protocol_residue
from app.services.stream.provider_content_filter import (
    CONTENT_FILTER_FINISH_REASON,
    finish_content_filtered_round,
    is_content_filter_error,
    replace_with_content_filtered_block,
)
from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    build_research_repair_prompt,
    validate_research_completion,
)
from app.services.stream.safe_fallback_response import default_safe_fallback, render_safe_fallback
from app.services.stream.tool_ban import forbid_tool_calls, without_tool_definitions
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset
from app.services.stream_state_service import StreamOwnershipLostError, StreamWriteTerminalError, append_chunk
from app.utils.prompt_fingerprint import fingerprint_system_messages

if TYPE_CHECKING:
    from app.services.stream.run_capability_router import RunCapabilityResolution
    from app.services.stream.safe_fallback_response import FallbackResponseContext

LIMIT_SUMMARY_PROMPT = _LIMIT_SUMMARY_PROMPT


def _accepts_keyword(fn: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values())


@dataclass(frozen=True)
class LimitSummaryOutcome:
    accumulated_usage: Usage
    context: ContextUsage | None = None
    incomplete: bool = False


@dataclass(frozen=True)
class LimitSummaryRoundResult:
    reasoning_buf: str
    content_buf: str
    usage_data: Usage | None
    context: ContextUsage | None = None
    tool_calls: tuple[dict, ...] = ()
    finish_reason: str = "stop"
    llm_lifecycle: LLMRoundLifecycle | None = None


@dataclass(frozen=True)
class LimitSummaryStepRequest:
    conversation_id: str
    task_id: str
    run_id: str
    step_number: int
    model_id: str
    provider: str
    litellm_model: str
    litellm_kwargs: dict
    messages: list[PromptMessage]
    should_use_reasoning: bool
    content_blocks: list
    call_kwargs: dict
    accumulated_usage: Usage
    emitter: Any
    session_cache: Any
    total_timeout_s: int
    run_start: float
    start_step_fn: Callable[..., Awaitable[Any]]
    complete_step_fn: Callable[..., Awaitable[Any]]
    llm_call_fn: Callable[..., Awaitable[Any]]
    stream_round_fn: Callable[..., Awaitable[tuple[str, str, list[dict], str, Usage | None]]]
    log_round_summary_fn: Callable[..., None]
    warning_fn: Callable[[str], None] | None = None
    clock: Callable[[], float] = time.time
    on_step_started: Callable[[str], None] | None = None
    on_context_updated: Callable[[ContextUsage], None] | None = None
    assistant_message_id: str | None = None
    summary_finish_reason: str = "limit_summary"
    task_mode: str = "standard"
    evidence_policy: str = "standard"
    research_workset: ResearchEvidenceWorkset | None = None
    defer_output: bool = True
    llm_round_detail_scheduler: Callable[[Any], Any] | None = None
    capability_resolution: RunCapabilityResolution | None = None
    recovery_evidence: RecoveryEvidenceWorkset | None = None
    fallback_response_context: FallbackResponseContext | None = None
    # 本次 run 已写出文档时，总结只做简短回复，不再复述文档正文。
    document_delivered: bool = False
    # 历史轮次回放的工具调用 id；总结无视工具禁令后的重做不带工具定义，需移除这些事务。
    history_tool_call_ids: frozenset[str] = frozenset()


def _should_defer_summary_output(request: LimitSummaryStepRequest) -> bool:
    return request.defer_output or request.task_mode == "deep_research"


def build_limit_summary_call_kwargs(call_kwargs: dict) -> dict:
    """总结不允许调用工具：有工具定义时照常公告并禁止调用，保留历史工具事务，见 tool_ban。"""
    tools = call_kwargs.get("tools")
    base = without_tool_definitions(call_kwargs)
    return forbid_tool_calls(base, tools) if tools else base


def compute_summary_timeout(*, total_timeout_s: int, run_start: float, clock: Callable[[], float]) -> float:
    return max(10, total_timeout_s - (clock() - run_start))


def append_limit_summary_prompt(
    messages: list[PromptMessage | dict],
    *,
    summary_finish_reason: str = "limit_summary",
    task_mode: str = "standard",
    evidence_policy: str = "standard",
    content_blocks: list | None = None,
    capability_resolution: RunCapabilityResolution | None = None,
    recovery_evidence: RecoveryEvidenceWorkset | None = None,
    document_delivered: bool = False,
) -> None:
    messages[:] = ensure_prompt_messages(messages)
    if summary_finish_reason == "no_progress_summary":
        prompt = NO_PROGRESS_SUMMARY_PROMPT
        section_id = NO_PROGRESS_SUMMARY
    elif summary_finish_reason == "research_evidence_repair_exhausted":
        prompt = RESEARCH_EVIDENCE_SUMMARY_PROMPT
        section_id = RESEARCH_EVIDENCE_SUMMARY
    else:
        prompt = get_limit_summary_prompt()
        section_id = LIMIT_SUMMARY
    if task_mode == "deep_research" and section_id != RESEARCH_EVIDENCE_SUMMARY:
        prompt = f"{prompt}\n\n{RESEARCH_EVIDENCE_SUMMARY_PROMPT}"
    # 一次工具证据都没有时，"基于已收集的信息"指向空集；补上诚实下限，避免用参数记忆补齐。
    if content_blocks is not None and not has_tool_evidence(content_blocks, recovery_evidence=recovery_evidence):
        prompt = f"{prompt}\n\n{NO_TOOL_EVIDENCE_SUMMARY_PROMPT}"
    if document_delivered:
        prompt = f"{prompt}\n\n{render_runtime_prompt('documents.delivered_summary')}"
    messages.append(PromptMessage(role="system", content=prompt, section_id=section_id))


def remove_conflicting_tool_usage_contract(
    messages: list[PromptMessage | dict],
    *,
    task_mode: str = "standard",
    final_synthesis: bool = False,
    preserve_web_tool_context: bool = False,
) -> None:
    """收尾总结移除会继续诱发工具协议的旧契约与事务历史。"""

    del final_synthesis  # 终局总结统一清理控制契约，不再按结束原因分叉。
    normalized = ensure_prompt_messages(messages)
    # 查证总结要保留已格式化的读页正文，并保持 assistant/tool 消息成对。
    strip_tool_transactions = task_mode == "deep_research" or (
        not preserve_web_tool_context and _only_recoverable_tool_transactions(messages)
    )
    filtered: list[PromptMessage] = []
    for message in normalized:
        role = message.role
        if role == "system" and is_terminal_control_section(message.section_id):
            continue
        if (
            task_mode == "deep_research"
            and role == "system"
            and message.section_id in DEEP_RESEARCH_CONTROL_SECTION_IDS
        ):
            continue
        if strip_tool_transactions and role == "assistant" and message.get("tool_calls"):
            continue
        if strip_tool_transactions and role == "tool":
            continue
        filtered.append(message)
    messages[:] = filtered


def _only_recoverable_tool_transactions(messages: list[PromptMessage | dict]) -> bool:
    """仅当全部事务都有服务端安全投影时，才从普通总结上下文移除原始协议。"""

    recoverable_tool_names = {"update_plan", "web_search", "url_read"}
    tool_call_names: list[str] = []
    tool_call_ids: set[str] = set()
    tool_message_ids: set[str] = set()
    for message in messages:
        if message.get("role") == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if not tool_call_id:
                return False
            tool_message_ids.add(tool_call_id)
        if message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                return False
            function = tool_call.get("function")
            if isinstance(function, dict):
                tool_name = str(function.get("name") or "")
            else:
                tool_name = str(tool_call.get("name") or "")
            if not tool_name:
                return False
            tool_call_id = str(tool_call.get("id") or "")
            if not tool_call_id:
                return False
            tool_call_ids.add(tool_call_id)
            tool_call_names.append(tool_name)

    if not tool_call_names:
        return False
    return tool_call_ids == tool_message_ids and all(
        tool_name in recoverable_tool_names for tool_name in tool_call_names
    )


SUMMARY_TOOL_PROTOCOL_RETRY_PROMPT = render_runtime_prompt("stream.summary_tool_protocol_retry")

SUMMARY_PROTOCOL_FALLBACK_TEXT = default_safe_fallback("protocol_error")
DEEP_RESEARCH_INCOMPLETE_TEXT = (
    "本次研究尚未完成，当前取得的可核验依据不足，暂时无法给出可靠结论。你可以稍后重试，或缩小研究范围后重新发起。"
)


def _create_limit_summary_observation(
    *,
    request: LimitSummaryStepRequest,
    context_plan: ContextPlan,
    step_id: str,
    call_kwargs: dict,
    round_index: int | None = None,
    estimator_status: str | None = None,
) -> Any:
    round_kind = "limit_summary"
    return create_llm_round_observation(
        conversation_id=request.conversation_id,
        run_id=request.run_id,
        round_index=round_index or request.step_number,
        step_id=step_id,
        round_kind=round_kind,
        model_id=request.model_id,
        provider=request.provider,
        litellm_model=request.litellm_model,
        messages=to_provider_messages(context_plan.messages),
        call_kwargs=call_kwargs,
        assistant_message_id=request.assistant_message_id,
        context_management=context_plan.telemetry(),
        estimated_prompt_tokens=context_plan.estimated_tokens_after,
        estimator_status=estimator_status,
    )


async def call_limit_summary_round(
    *,
    request: LimitSummaryStepRequest,
    thinking_block_id: str,
    text_block_id: str,
    step_id: str,
    partial_output: dict[str, str] | None = None,
    round_index: int | None = None,
    forbid_tools: bool = True,
) -> LimitSummaryRoundResult:
    """forbid_tools=False 用于模型已无视工具禁令后的重做：不带工具定义，去掉历史工具事务。"""
    call_kwargs = build_limit_summary_call_kwargs(request.call_kwargs)
    messages = request.messages
    if not forbid_tools:
        call_kwargs = without_tool_definitions(call_kwargs)
        messages = without_tool_transactions(messages, request.history_tool_call_ids)
    return await _call_limit_summary_round_once(
        request=request,
        thinking_block_id=thinking_block_id,
        text_block_id=text_block_id,
        step_id=step_id,
        call_kwargs=call_kwargs,
        messages=messages,
        partial_output=partial_output,
        round_index=round_index,
    )


async def _call_limit_summary_round_once(
    *,
    request: LimitSummaryStepRequest,
    thinking_block_id: str,
    text_block_id: str,
    step_id: str,
    call_kwargs: dict,
    messages: list[PromptMessage],
    partial_output: dict[str, str] | None = None,
    round_index: int | None = None,
) -> LimitSummaryRoundResult:
    final_call_kwargs = configure_reasoning_call_kwargs(
        call_kwargs,
        provider=request.provider,
        should_use_reasoning=request.should_use_reasoning,
    )
    finalized_messages = finalize_model_call_language_policy(messages)
    try:
        context_plan = await prepare_context(
            messages=finalized_messages,
            model_id=request.model_id,
            litellm_model=request.litellm_model,
            call_kwargs=final_call_kwargs,
        )
    except ContextManagementError as error:
        error_context = build_context_usage(error.plan, round_index=request.step_number)
        if request.on_context_updated is not None:
            request.on_context_updated(error_context)
        await emit_context_status(request.emitter, phase="error", context=error_context)
        observation = _create_limit_summary_observation(
            request=request,
            context_plan=error.plan,
            step_id=step_id,
            call_kwargs=final_call_kwargs,
            round_index=round_index,
            estimator_status="context_manager_error",
        )
        observation.start()
        await observation.finish_error(error)
        raise
    effective_messages = context_plan.messages
    estimated_context = build_context_usage(context_plan, round_index=request.step_number)
    if request.on_context_updated is not None:
        request.on_context_updated(estimated_context)
    await emit_context_status(request.emitter, phase="estimated", context=estimated_context)
    observation = _create_limit_summary_observation(
        request=request,
        context_plan=context_plan,
        step_id=step_id,
        call_kwargs=final_call_kwargs,
        round_index=round_index,
    )
    lifecycle = await LLMRoundLifecycle.start(
        emitter=request.emitter,
        observation=observation,
        round_index=round_index or request.step_number,
        model=request.model_id,
        provider=request.provider,
        parent_step_id=step_id,
        text_block_id=text_block_id,
        conversation_id=request.conversation_id,
        run_id=request.run_id,
        message_id=request.assistant_message_id,
        detail_scheduler=request.llm_round_detail_scheduler,
        system_prompt_fingerprint=fingerprint_system_messages(to_provider_messages(effective_messages)),
        context_visibility=context_plan.tool_visibility(finalized_messages),
        tool_names=round_tool_names(final_call_kwargs),
    )
    observation.start()
    detail_partial_output = partial_output if partial_output is not None else {}
    try:
        response = await request.llm_call_fn(
            request.litellm_model,
            request.litellm_kwargs,
            to_provider_messages(effective_messages),
            **final_call_kwargs,
        )
        response = observation.wrap_response(response)
        stream_kwargs = {"run_id": request.run_id, "step_id": step_id}
        if _accepts_keyword(request.stream_round_fn, "provider"):
            stream_kwargs["provider"] = request.provider
        if _accepts_keyword(request.stream_round_fn, "defer_output"):
            stream_kwargs["defer_output"] = _should_defer_summary_output(request)
        if (
            request.should_use_reasoning
            and _should_defer_summary_output(request)
            and _accepts_keyword(request.stream_round_fn, "allow_deferred_reasoning_output")
        ):
            stream_kwargs["allow_deferred_reasoning_output"] = True
        if _accepts_keyword(request.stream_round_fn, "partial_output"):
            stream_kwargs["partial_output"] = detail_partial_output
        visible_callback_supported = _accepts_keyword(request.stream_round_fn, "on_visible_output")
        if lifecycle is not None and not _should_defer_summary_output(request) and visible_callback_supported:
            stream_kwargs["on_visible_output"] = lifecycle.publish_visible_output
        candidate_callback = getattr(observation, "observe_output_candidate", None)
        if callable(candidate_callback) and _accepts_keyword(request.stream_round_fn, "on_output_candidate"):
            stream_kwargs["on_output_candidate"] = candidate_callback
        capture_candidate_time = getattr(observation, "capture_output_candidate_time", None)
        if callable(capture_candidate_time) and _accepts_keyword(
            request.stream_round_fn,
            "capture_output_candidate_time",
        ):
            stream_kwargs["capture_output_candidate_time"] = capture_candidate_time
        reasoning_buf, content_buf, tool_calls, finish_reason, usage_data = await request.stream_round_fn(
            response,
            request.conversation_id,
            request.task_id,
            request.should_use_reasoning,
            thinking_block_id,
            text_block_id,
            **stream_kwargs,
        )
    except asyncio.CancelledError as exc:
        if lifecycle is not None:
            lifecycle.record_detail(
                reasoning_text=detail_partial_output.get("reasoning_buf", ""),
                content_text=detail_partial_output.get("content_buf", ""),
            )
        await _close_summary_round_after_primary_error(
            observation=observation,
            lifecycle=lifecycle,
            error=exc,
        )
        raise
    except BaseException as exc:
        if lifecycle is not None:
            lifecycle.record_detail(
                reasoning_text=detail_partial_output.get("reasoning_buf", ""),
                content_text=detail_partial_output.get("content_buf", ""),
            )
        await _close_summary_round_after_primary_error(
            observation=observation,
            lifecycle=lifecycle,
            error=exc,
        )
        if is_content_filter_error(exc):
            return LimitSummaryRoundResult(
                reasoning_buf="",
                content_buf="",
                usage_data=None,
                finish_reason=CONTENT_FILTER_FINISH_REASON,
            )
        raise
    try:
        freeze = getattr(observation, "freeze", None)
        if callable(freeze):
            freeze()
        final_context = build_context_usage(context_plan, usage_data, round_index=request.step_number)
        if request.on_context_updated is not None:
            request.on_context_updated(final_context)
        await emit_context_status(request.emitter, phase="final", context=final_context)
        await observation.finish_success(usage=usage_data, finish_reason=finish_reason)
        if lifecycle is not None:
            lifecycle.record_detail(reasoning_text=reasoning_buf, content_text=content_buf)
            lifecycle.record_result(usage=usage_data, finish_reason=finish_reason)
            if finish_reason == "cancelled":
                await lifecycle.finish_cancelled(reason="superseded")
            elif _should_defer_summary_output(request) and tool_calls:
                await lifecycle.publish_tool_output()
            elif not _should_defer_summary_output(request):
                if tool_calls:
                    await lifecycle.publish_tool_output()
                elif content_buf:
                    await lifecycle.publish_visible_output("content")
                elif reasoning_buf:
                    await lifecycle.publish_visible_output("reasoning")
                await lifecycle.finish_success(output_visible=False)
        request.log_round_summary_fn(
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            step_number=request.step_number,
            model_id=request.model_id,
            provider=request.provider,
            finish_reason=request.summary_finish_reason,
            tool_calls_count=len(tool_calls),
            reasoning_buf=reasoning_buf,
            content_buf=content_buf,
        )
        return LimitSummaryRoundResult(
            reasoning_buf=reasoning_buf,
            content_buf=content_buf,
            usage_data=usage_data,
            context=final_context,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            llm_lifecycle=(lifecycle if lifecycle is not None and not lifecycle.terminal_emitted else None),
        )
    except asyncio.CancelledError as exc:
        await _close_summary_round_after_primary_error(
            observation=observation,
            lifecycle=lifecycle,
            error=exc,
        )
        raise
    except BaseException as exc:
        await _close_summary_round_after_primary_error(
            observation=observation,
            lifecycle=lifecycle,
            error=exc,
        )
        raise


def accumulate_summary_usage(accumulated_usage: Usage, usage_data: Usage | None) -> Usage:
    return accumulate_token_usage(accumulated_usage, usage_data)


async def _close_summary_round_after_primary_error(
    *, observation: Any, lifecycle: LLMRoundLifecycle | None, error: BaseException
) -> None:
    freeze = getattr(observation, "freeze", None)
    if callable(freeze):
        freeze()
    try:
        await observation.finish_error(error)
    except BaseException as secondary:
        logger.warning("总结 LLM 观测异常收尾失败，保留主异常: error_type=%s", type(secondary).__name__)
    if lifecycle is None:
        return
    try:
        if isinstance(error, (asyncio.CancelledError, StreamOwnershipLostError)):
            await lifecycle.finish_cancelled(reason="shutdown")
        elif is_content_filter_error(error):
            await finish_content_filtered_round(lifecycle)
        else:
            await lifecycle.finish_failed(error)
    except BaseException as secondary:
        logger.warning("总结 LLM 生命周期异常收尾失败，保留主异常: error_type=%s", type(secondary).__name__)


def append_summary_content_blocks(
    *,
    content_blocks: list,
    content_buf: str,
    text_block_id: str,
) -> None:
    if content_buf:
        content_blocks.append(TextBlock(type="text", id=text_block_id, text=content_buf))


async def complete_limit_summary_step(
    *,
    summary_context: Any,
    emitter: Any,
    session_cache: Any,
    complete_step_fn: Callable[..., Awaitable[Any]],
    clock: Callable[[], float],
) -> None:
    await complete_step_fn(
        context=summary_context,
        emitter=emitter,
        session_cache=session_cache,
        tool_names=[],
        tool_call_count=0,
        clock=clock,
    )


async def start_limit_summary_step(*, request: LimitSummaryStepRequest) -> Any:
    return await request.start_step_fn(
        emitter=request.emitter,
        session_cache=request.session_cache,
        run_id=request.run_id,
        step_number=request.step_number,
        clock=request.clock,
        on_step_started=request.on_step_started,
    )


async def run_summary_round_with_timeout(
    *,
    request: LimitSummaryStepRequest,
    summary_context: Any,
    thinking_block_id: str,
    text_block_id: str,
    remaining: float,
) -> LimitSummaryRoundResult:
    started_at = time.monotonic()
    next_round_index = request.step_number
    try:
        first_result = await asyncio.wait_for(
            call_limit_summary_round(
                request=request,
                thinking_block_id=thinking_block_id,
                text_block_id=text_block_id,
                step_id=summary_context.step_id,
                partial_output=None,
                round_index=next_round_index,
            ),
            timeout=remaining,
        )
        next_round_index += 1
    except asyncio.TimeoutError:
        warning = request.warning_fn if request.warning_fn is not None else logger.warning
        warning(f"触顶总结超出剩余预算: conv_id={request.conversation_id}, budget={remaining}s")
        return _build_timeout_partial_result({})
    except StreamWriteTerminalError:
        raise

    if not _is_summary_tool_protocol_violation(first_result):
        result = first_result
    else:
        await _finish_summary_round_lifecycle(first_result, model_output_visible=False)
        warning = request.warning_fn if request.warning_fn is not None else logger.warning
        warning(
            "无工具收尾总结返回了工具协议，执行一次无工具重试: "
            f"conv_id={request.conversation_id}, run_id={request.run_id}, step={request.step_number}"
        )
        request.messages.append(
            PromptMessage(
                role="system",
                content=SUMMARY_TOOL_PROTOCOL_RETRY_PROMPT,
                section_id=SUMMARY_TOOL_PROTOCOL_RETRY,
            )
        )
        retry_remaining = remaining - (time.monotonic() - started_at)
        if retry_remaining <= 0:
            return _build_streamed_retry_failure(
                request=request,
                first_result=first_result,
            )

        try:
            retry_result = await asyncio.wait_for(
                call_limit_summary_round(
                    request=request,
                    thinking_block_id=thinking_block_id,
                    text_block_id=text_block_id,
                    step_id=summary_context.step_id,
                    partial_output=None,
                    round_index=next_round_index,
                    forbid_tools=False,
                ),
                timeout=retry_remaining,
            )
            next_round_index += 1
        except asyncio.TimeoutError:
            warning(
                "无工具收尾重试超出剩余预算，使用安全失败文案: "
                f"conv_id={request.conversation_id}, budget={retry_remaining}s"
            )
            return _build_streamed_retry_failure(
                request=request,
                first_result=first_result,
            )
        except StreamWriteTerminalError:
            raise

        usage_data = _combine_optional_usage(first_result.usage_data, retry_result.usage_data)
        if _is_summary_tool_protocol_violation(retry_result):
            await _finish_summary_round_lifecycle(retry_result, model_output_visible=False)
            warning(
                "无工具收尾重试仍返回工具协议，使用安全失败文案: "
                f"conv_id={request.conversation_id}, run_id={request.run_id}, step={request.step_number}"
            )
            return _build_streamed_retry_failure(
                request=request,
                first_result=first_result,
                retry_result=retry_result,
                usage_data=usage_data,
            )
        result = LimitSummaryRoundResult(
            reasoning_buf=first_result.reasoning_buf + retry_result.reasoning_buf,
            content_buf=retry_result.content_buf,
            usage_data=usage_data,
            context=retry_result.context,
            tool_calls=(),
            finish_reason=retry_result.finish_reason,
            llm_lifecycle=retry_result.llm_lifecycle,
        )

    return await _repair_deep_research_summary_citations(
        request=request,
        summary_context=summary_context,
        thinking_block_id=thinking_block_id,
        text_block_id=text_block_id,
        result=result,
        remaining=remaining - (time.monotonic() - started_at),
        round_index=next_round_index,
    )


async def _repair_deep_research_summary_citations(
    *,
    request: LimitSummaryStepRequest,
    summary_context: Any,
    thinking_block_id: str,
    text_block_id: str,
    result: LimitSummaryRoundResult,
    remaining: float,
    round_index: int,
) -> LimitSummaryRoundResult:
    """证据充足但最终引用缺失或越界时，允许一次无工具引用修正。"""

    if request.task_mode != "deep_research" or result.finish_reason == CONTENT_FILTER_FINISH_REASON:
        return result
    workset = request.research_workset or ResearchEvidenceWorkset()
    validation = validate_research_completion(workset, result.content_buf)
    if validation.is_valid or validation.reason not in {"missing_citation", "invalid_citation"}:
        return result

    warning = request.warning_fn if request.warning_fn is not None else logger.warning
    warning(
        "深度研究收尾引用校验未通过，执行一次无工具引用修正: "
        f"conv_id={request.conversation_id}, run_id={request.run_id}, "
        f"step={request.step_number}, reason={validation.reason}"
    )
    if remaining <= 0:
        return result
    request.messages.append(
        PromptMessage(
            role="system",
            content=build_research_repair_prompt(validation.reason, workset),
            section_id=RESEARCH_COMPLETION_REPAIR,
        )
    )
    if result.llm_lifecycle is not None:
        result.llm_lifecycle.suppress_output("research_guard")
    await _finish_summary_round_lifecycle(result, model_output_visible=False)
    try:
        repaired = await asyncio.wait_for(
            call_limit_summary_round(
                request=request,
                thinking_block_id=thinking_block_id,
                text_block_id=text_block_id,
                step_id=summary_context.step_id,
                round_index=round_index,
            ),
            timeout=remaining,
        )
    except asyncio.TimeoutError:
        warning(f"深度研究收尾引用修正超出剩余预算: conv_id={request.conversation_id}, budget={remaining}s")
        return result
    if _is_summary_tool_protocol_violation(repaired):
        await _finish_summary_round_lifecycle(repaired, model_output_visible=False)
        warning(
            "深度研究收尾引用修正返回工具协议，保留原候选等待安全门禁: "
            f"conv_id={request.conversation_id}, run_id={request.run_id}, step={request.step_number}"
        )
        return result
    return LimitSummaryRoundResult(
        reasoning_buf=repaired.reasoning_buf,
        content_buf=repaired.content_buf,
        usage_data=_combine_optional_usage(result.usage_data, repaired.usage_data),
        context=repaired.context,
        tool_calls=(),
        finish_reason=repaired.finish_reason,
        llm_lifecycle=repaired.llm_lifecycle,
    )


def _combine_optional_usage(*items: Usage | None) -> Usage | None:
    present = [item for item in items if item is not None]
    if not present:
        return None
    combined = present[0]
    for item in present[1:]:
        combined = accumulate_token_usage(combined, item)
    return combined


def _is_summary_tool_protocol_violation(result: LimitSummaryRoundResult) -> bool:
    return bool(result.tool_calls) or result.finish_reason == "tool_protocol_error"


def _record_summary_output(result: LimitSummaryRoundResult, answer: str, reason: str) -> None:
    """正文成功写出后，用原候选识别所有总结兜底及编辑。"""
    lifecycle = result.llm_lifecycle
    if lifecycle is None or lifecycle.terminal_emitted:
        return
    unchanged = answer == lifecycle.content_text
    lifecycle.record_output(
        disposition="emitted" if unchanged else "replaced",
        source="model" if unchanged else "server",
        reason="deferred" if unchanged else reason,
    )


async def _finish_summary_round_lifecycle(
    result: LimitSummaryRoundResult,
    *,
    model_output_visible: bool,
) -> None:
    lifecycle = result.llm_lifecycle
    if lifecycle is None:
        return
    if model_output_visible:
        await lifecycle.publish_visible_output("content")
    await lifecycle.finish_success(output_visible=False)


def _build_summary_protocol_fallback(
    result: LimitSummaryRoundResult,
    *,
    usage_data: Usage | None = None,
) -> LimitSummaryRoundResult:
    return LimitSummaryRoundResult(
        reasoning_buf=result.reasoning_buf,
        content_buf=SUMMARY_PROTOCOL_FALLBACK_TEXT,
        usage_data=result.usage_data if usage_data is None else usage_data,
        context=result.context,
        tool_calls=(),
        finish_reason="protocol_fallback",
        llm_lifecycle=(
            result.llm_lifecycle
            if result.llm_lifecycle is not None and not result.llm_lifecycle.terminal_emitted
            else None
        ),
    )


def _build_timeout_partial_result(partial_output: dict[str, str]) -> LimitSummaryRoundResult:
    reasoning_buf = partial_output.get("reasoning_buf", "")
    content_buf = partial_output.get("content_buf", "")
    return LimitSummaryRoundResult(
        reasoning_buf=reasoning_buf,
        content_buf=content_buf,
        usage_data=None,
        finish_reason="timeout_partial" if reasoning_buf or content_buf else "timeout",
    )


def _build_streamed_retry_failure(
    *,
    request: LimitSummaryStepRequest,
    first_result: LimitSummaryRoundResult,
    retry_result: LimitSummaryRoundResult | None = None,
    usage_data: Usage | None = None,
) -> LimitSummaryRoundResult:
    fallback_result = retry_result or first_result
    if retry_result is not None:
        fallback_result = LimitSummaryRoundResult(
            reasoning_buf=first_result.reasoning_buf + retry_result.reasoning_buf,
            content_buf=retry_result.content_buf,
            usage_data=retry_result.usage_data,
            context=retry_result.context,
            tool_calls=(),
            finish_reason=retry_result.finish_reason,
            llm_lifecycle=retry_result.llm_lifecycle,
        )
    return _build_summary_protocol_fallback(
        fallback_result,
        usage_data=usage_data,
    )


async def run_limit_summary_step(
    *,
    request: LimitSummaryStepRequest,
) -> LimitSummaryOutcome:
    summary_context = await start_limit_summary_step(request=request)

    remove_conflicting_tool_usage_contract(
        request.messages,
        task_mode=request.task_mode,
        final_synthesis=True,
    )
    append_limit_summary_prompt(
        request.messages,
        summary_finish_reason=request.summary_finish_reason,
        task_mode=request.task_mode,
        evidence_policy=request.evidence_policy,
        content_blocks=request.content_blocks,
        capability_resolution=request.capability_resolution,
        recovery_evidence=request.recovery_evidence,
        document_delivered=request.document_delivered,
    )
    thinking_block_id = summary_context.thinking_block_id
    text_block_id = summary_context.text_block_id
    remaining = compute_summary_timeout(
        total_timeout_s=request.total_timeout_s,
        run_start=request.run_start,
        clock=request.clock,
    )

    round_result = await run_summary_round_with_timeout(
        request=request,
        summary_context=summary_context,
        thinking_block_id=thinking_block_id,
        text_block_id=text_block_id,
        remaining=remaining,
    )

    next_usage = accumulate_summary_usage(request.accumulated_usage, round_result.usage_data)
    primary_error: BaseException | None = None
    try:
        incomplete = await _commit_limit_summary_result(
            request=request,
            round_result=round_result,
            summary_context=summary_context,
            thinking_block_id=thinking_block_id,
            text_block_id=text_block_id,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await _finish_summary_round_lifecycle(round_result, model_output_visible=False)
        except BaseException as secondary_error:
            if primary_error is None:
                raise
            try:
                logger.warning(
                    "deferred 总结生命周期收尾失败，保留主异常: error_type=%s error_code=deferred_terminal_failure",
                    type(secondary_error).__name__,
                )
            except BaseException:
                pass
    await complete_limit_summary_step(
        summary_context=summary_context,
        emitter=request.emitter,
        session_cache=request.session_cache,
        complete_step_fn=request.complete_step_fn,
        clock=request.clock,
    )
    return LimitSummaryOutcome(
        accumulated_usage=next_usage,
        context=round_result.context,
        incomplete=incomplete,
    )


async def _safe_summary_fallback(request: LimitSummaryStepRequest, reason: str) -> str:
    context = request.fallback_response_context
    remaining = 0.0
    if context is not None and context.original_message:
        remaining = request.total_timeout_s - max(0.0, request.clock() - request.run_start)
    return await render_safe_fallback(reason, context=context, model_id=request.model_id, timeout_s=remaining)


async def _guard_protocol_residue(
    request: LimitSummaryStepRequest,
    answer: str,
) -> tuple[str, bool]:
    """未执行的工具协议不是答案，收尾总结同样不能放行。

    与无证据守卫的边界不同：那一条「有任何工具证据就放行」，而协议残留与证据无关——
    真实样本 Run e96ab8c9 正是搜索成功、证据齐备，正文却是一段没执行的更新计划协议。
    """

    if not contains_tool_protocol_residue(answer):
        return answer, False
    if request.warning_fn is not None:
        request.warning_fn(
            "收尾总结正文仍是未执行的工具协议，已替换为诚实答复: "
            f"conv_id={request.conversation_id} run_id={request.run_id} "
            f"finish_reason={request.summary_finish_reason}"
        )
    return await _safe_summary_fallback(request, "protocol_error"), True


async def _commit_limit_summary_result(
    *,
    request: LimitSummaryStepRequest,
    round_result: LimitSummaryRoundResult,
    summary_context: Any,
    thinking_block_id: str,
    text_block_id: str,
) -> bool:
    if round_result.finish_reason == CONTENT_FILTER_FINISH_REASON:
        if request.warning_fn is not None:
            request.warning_fn(
                "收尾总结被模型服务商内容审核拦截，整条回复替换为固定提示: "
                f"conv_id={request.conversation_id} run_id={request.run_id} model_id={request.model_id}"
            )
        if round_result.llm_lifecycle is not None:
            round_result.llm_lifecycle.record_output(disposition="replaced", source="server", reason="content_filtered")
        await replace_with_content_filtered_block(content_blocks=request.content_blocks, emitter=request.emitter)
        return False
    if round_result.reasoning_buf:
        request.content_blocks.append(
            ThinkingBlock(
                type="thinking",
                id=thinking_block_id,
                thinking=round_result.reasoning_buf,
            )
        )

    if request.task_mode == "deep_research":
        return await _complete_deep_research_summary(
            request=request,
            round_result=round_result,
            thinking_block_id=thinking_block_id,
            text_block_id=text_block_id,
            step_id=summary_context.step_id,
        )
    else:
        answer = round_result.content_buf.strip()
        incomplete = round_result.finish_reason == "protocol_fallback" or not answer
        if not answer or round_result.finish_reason == "protocol_fallback":
            answer = await _safe_summary_fallback(request, "protocol_error")
        answer, protocol_residue = await _guard_protocol_residue(request, answer)
        if protocol_residue:
            incomplete = True
        if _should_defer_summary_output(request):
            await append_chunk(
                request.conversation_id,
                "answering",
                answer,
                text_block_id,
                task_id=request.task_id,
                run_id=request.run_id,
                step_id=summary_context.step_id,
            )
            _record_summary_output(round_result, answer, "summary_guard")
            if (
                round_result.content_buf.strip()
                and round_result.finish_reason != "protocol_fallback"
                and not protocol_residue
            ):
                await _finish_summary_round_lifecycle(round_result, model_output_visible=True)
        # 持久化必须使用实际交付的正文，不能重新采用本地化之前的模型候选或旧默认文案。
        append_summary_content_blocks(
            content_blocks=request.content_blocks,
            content_buf=answer,
            text_block_id=text_block_id,
        )
        return incomplete


async def _complete_deep_research_summary(
    *,
    request: LimitSummaryStepRequest,
    round_result: LimitSummaryRoundResult,
    thinking_block_id: str,
    text_block_id: str,
    step_id: str,
) -> bool:
    workset = request.research_workset or ResearchEvidenceWorkset()
    validation = validate_research_completion(workset, round_result.content_buf)
    answer = round_result.content_buf.strip() if validation.is_valid else DEEP_RESEARCH_INCOMPLETE_TEXT
    if not validation.is_valid:
        warning = request.warning_fn if request.warning_fn is not None else logger.warning
        warning(
            "深度研究收尾未通过安全门禁: "
            f"conv_id={request.conversation_id}, run_id={request.run_id}, "
            f"step={request.step_number}, research_validation_reason={validation.reason}"
        )
    await append_chunk(
        request.conversation_id,
        "answering",
        answer,
        text_block_id,
        task_id=request.task_id,
        run_id=request.run_id,
        step_id=step_id,
    )
    _record_summary_output(round_result, answer, "research_guard")
    if validation.is_valid:
        await _finish_summary_round_lifecycle(round_result, model_output_visible=True)
    append_summary_content_blocks(
        content_blocks=request.content_blocks,
        content_buf=answer,
        text_block_id=text_block_id,
    )
    if not validation.is_valid:
        return True
    await _emit_deep_summary_used_evidence(
        request=request,
        answer_text=answer,
        workset=workset,
    )
    return False


async def _emit_deep_summary_used_evidence(
    *,
    request: LimitSummaryStepRequest,
    answer_text: str,
    workset: ResearchEvidenceWorkset,
) -> None:
    emit = getattr(request.emitter, "evidence_item_upserted", None)
    if emit is None:
        return
    try:
        evidence_items = build_used_final_answer_evidence(
            content_blocks=request.content_blocks,
            answer_text=answer_text,
            evidence_policy=request.evidence_policy,
            allowed_citation_indexes=workset.valid_citation_indexes,
        )
        for evidence in evidence_items:
            await emit(tool_call_id=None, evidence=evidence)
    except StreamWriteTerminalError:
        raise
    except Exception as error:  # noqa: BLE001 — used evidence 观测失败不能覆盖安全收尾
        warning = request.warning_fn if request.warning_fn is not None else logger.warning
        warning(f"深度研究总结 used evidence 发送失败: error_type={type(error).__name__}")
