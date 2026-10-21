"""Agent loop round outcome 分发。"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.prompts.prompt_message import PromptMessage
from app.core.logger import app_logger as logger
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.agent_loop_outcome import AgentLoopExit, AgentLoopOutcome
from app.services.stream.agent_loop_runtime import AgentLoopRuntime
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_loop_step_requests import build_tool_round_request
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.llm_stream import contains_tool_protocol_residue
from app.services.stream.provider_content_filter import (
    CONTENT_FILTER_FINISH_REASON,
    replace_with_content_filtered_block,
)
from app.services.stream.round_completion import append_round_content_blocks, complete_text_response_step
from app.services.stream.safe_fallback_response import PROTOCOL_FALLBACK_TEXT
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream_state_service import StreamWriteTerminalError


@dataclass(frozen=True)
class AgentRoundOutcomeRequest:
    db: object
    messages: list[PromptMessage]
    state: AgentLoopState
    runtime: AgentLoopRuntime
    step_number: int
    step_context: AgentStepContext
    round_result: AgentRoundResult


async def handle_agent_round_outcome(
    *,
    request: AgentRoundOutcomeRequest,
) -> AgentLoopOutcome | None:
    primary_error: BaseException | None = None
    try:
        return await _handle_agent_round_outcome(request=request)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        lifecycle = request.round_result.llm_lifecycle
        if lifecycle is not None:
            try:
                await lifecycle.finish_success(output_visible=False)
            except BaseException as secondary_error:
                if primary_error is None:
                    raise
                try:
                    logger.warning(
                        "Agent deferred LLM 生命周期收尾失败，保留主异常: "
                        "error_type=%s error_code=deferred_terminal_failure",
                        type(secondary_error).__name__,
                    )
                except BaseException:
                    pass


async def _handle_agent_round_outcome(
    *,
    request: AgentRoundOutcomeRequest,
) -> AgentLoopOutcome | None:
    if request.round_result.finish_reason == CONTENT_FILTER_FINISH_REASON:
        await _complete_content_filtered_round(request)
        return AgentLoopOutcome(exit=AgentLoopExit.COMPLETED)

    finish_reason = request.round_result.finish_reason
    if finish_reason == "stop":
        if _needs_empty_answer_summary(request):
            await _complete_empty_round_before_summary(request)
            return AgentLoopOutcome(exit=AgentLoopExit.SUMMARY_REQUIRED)
        await _complete_text_round(request)
        return AgentLoopOutcome(exit=AgentLoopExit.COMPLETED)

    if finish_reason == "cancelled":
        _append_round_blocks(request)
        return AgentLoopOutcome(exit=AgentLoopExit.SUPERSEDED, error_msg="被新请求取代")

    if finish_reason == "tool_calls" and request.round_result.tool_calls:
        return await _handle_tool_calls_round(request)

    if finish_reason == "tool_protocol_error":
        return await _complete_tool_protocol_error_round(request)

    await _complete_unknown_round(request)
    return AgentLoopOutcome(exit=AgentLoopExit.COMPLETED)


async def _reject_protocol_residue(request: AgentRoundOutcomeRequest, answer: str) -> str | None:
    """模型正文仍是未执行的工具协议时，它不能充当最终答复。

    这一层与证据无关。真实样本 Run e96ab8c9 搜索成功、证据齐备，
    正文却是一段没有执行的更新计划协议，运行还被标成了 stop / 已完成。判否与非成功终态必须落在同一处，
    只把文本换掉、仍标成功不算修好。

    不按中文工具名回译执行：识别放宽，恢复严格。
    """

    if not contains_tool_protocol_residue(answer):
        return None
    request.state.mark_unknown_terminated()
    request.runtime.warning_fn(
        "最终答复仍是未执行的工具协议，已替换并标记非成功终态: "
        f"conv_id={request.runtime.conversation_id}, run_id={request.runtime.run_id}, "
        f"step={request.step_number}"
    )
    return PROTOCOL_FALLBACK_TEXT


async def _complete_tool_protocol_error_round(
    request: AgentRoundOutcomeRequest,
) -> AgentLoopOutcome:
    """协议前缀不是最终答案，统一交给无工具总结做有界重试。"""

    _record_suppression(request, "tool_round")

    request.runtime.warning_fn(
        "模型工具协议无法解析，切换到无工具安全收口: "
        f"conv_id={request.runtime.conversation_id}, run_id={request.runtime.run_id}, "
        f"step={request.step_number}"
    )
    await complete_text_response_step(
        context=request.step_context,
        emitter=request.runtime.emitter,
        session_cache=request.runtime.session_cache,
        complete_step_fn=request.runtime.complete_step_fn,
        completed_tool_calls=request.state.total_tool_calls,
        max_tool_calls=request.runtime.limits.max_tool_calls,
        clock=request.runtime.clock,
    )
    return AgentLoopOutcome(
        exit=AgentLoopExit.SUMMARY_REQUIRED,
        summary_finish_reason="tool_protocol_error",
    )


def _append_round_blocks(request: AgentRoundOutcomeRequest) -> None:
    if request.round_result.output_deferred:
        return
    append_round_content_blocks(
        request.state.content_blocks,
        request.round_result.reasoning_buf,
        request.round_result.content_buf,
        request.step_context.thinking_block_id,
        request.step_context.text_block_id,
    )


async def _complete_text_round(request: AgentRoundOutcomeRequest) -> None:
    request = await _commit_deferred_answer(request)
    _append_round_blocks(request)
    await _emit_final_answer_used_evidence(request)
    await complete_text_response_step(
        context=request.step_context,
        emitter=request.runtime.emitter,
        session_cache=request.runtime.session_cache,
        complete_step_fn=request.runtime.complete_step_fn,
        completed_tool_calls=request.state.total_tool_calls,
        max_tool_calls=request.runtime.limits.max_tool_calls,
        clock=request.runtime.clock,
    )


async def _commit_deferred_answer(
    request: AgentRoundOutcomeRequest,
) -> AgentRoundOutcomeRequest:
    # 模型回答原样交付；空回答和触顶走模型的无工具总结，服务端不按产品结果拼回答。
    if not request.round_result.output_deferred:
        return request
    return await _commit_deferred_plain_answer(request)


async def _commit_deferred_plain_answer(request: AgentRoundOutcomeRequest) -> AgentRoundOutcomeRequest:
    answer = request.round_result.content_buf.strip()
    rejected = await _reject_protocol_residue(request, answer)
    if rejected is not None:
        await _append_committed_answer(request, rejected, output_reason="protocol_residue")
        return _with_replaced_answer(request, rejected)
    if answer:
        await _append_committed_answer(request, answer, model_output_visible=True)
    return _with_replaced_answer(request, answer)


def _record_suppression(request: AgentRoundOutcomeRequest, reason: str) -> None:
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        lifecycle.suppress_output(reason)


async def _append_committed_answer(
    request: AgentRoundOutcomeRequest,
    answer: str,
    *,
    model_output_visible: bool = False,
    output_reason: str | None = None,
) -> None:
    await request.runtime.output.append(
        "answering",
        answer,
        request.step_context.text_block_id,
        run_id=request.runtime.run_id,
        step_id=request.step_context.step_id,
    )
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        unchanged = model_output_visible and answer == request.round_result.content_buf
        reason = output_reason or ("deferred" if unchanged else "server_rewrite")
        lifecycle.record_output(
            disposition="emitted" if unchanged else "replaced",
            source="model" if unchanged else "server",
            reason=reason,
            block_id=request.step_context.text_block_id,
        )
        if model_output_visible:
            await lifecycle.publish_visible_output("content")


def _with_replaced_answer(
    request: AgentRoundOutcomeRequest,
    answer: str,
) -> AgentRoundOutcomeRequest:
    visible_reasoning = request.round_result.reasoning_buf
    if request.round_result.output_deferred and not request.round_result.allow_deferred_reasoning_output:
        visible_reasoning = ""
    return AgentRoundOutcomeRequest(
        db=request.db,
        messages=request.messages,
        state=request.state,
        runtime=request.runtime,
        step_number=request.step_number,
        step_context=request.step_context,
        round_result=AgentRoundResult(
            reasoning_buf=visible_reasoning,
            protocol_reasoning_buf=request.round_result.protocol_reasoning_buf,
            protocol_content_buf=request.round_result.protocol_content_buf,
            content_buf=answer,
            tool_calls=request.round_result.tool_calls,
            finish_reason=request.round_result.finish_reason,
            accumulated_usage=request.round_result.accumulated_usage,
            context=request.round_result.context,
            announced_tool_names=request.round_result.announced_tool_names,
            output_deferred=False,
            allow_deferred_reasoning_output=request.round_result.allow_deferred_reasoning_output,
            llm_lifecycle=request.round_result.llm_lifecycle,
        ),
    )


def _needs_empty_answer_summary(request: AgentRoundOutcomeRequest) -> bool:
    return (
        request.state.total_tool_calls > 0
        and not request.round_result.content_buf
        and not request.round_result.reasoning_buf
        and not request.round_result.tool_calls
    )


async def _complete_empty_round_before_summary(request: AgentRoundOutcomeRequest) -> None:
    request.runtime.warning_fn(
        "工具结果后模型返回空终态，切换到无工具收尾总结: "
        f"conv_id={request.runtime.conversation_id} run_id={request.runtime.run_id} "
        f"step={request.step_number} model_id={request.runtime.model_id}"
    )
    await complete_text_response_step(
        context=request.step_context,
        emitter=request.runtime.emitter,
        session_cache=request.runtime.session_cache,
        complete_step_fn=request.runtime.complete_step_fn,
        completed_tool_calls=request.state.total_tool_calls,
        max_tool_calls=request.runtime.limits.max_tool_calls,
        clock=request.runtime.clock,
    )


async def _handle_tool_calls_round(request: AgentRoundOutcomeRequest) -> AgentLoopOutcome | None:
    await _discard_streamed_tool_round_content(request)
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        lifecycle.suppress_output("tool_round")
        await lifecycle.finish_success(output_visible=False)
    await request.runtime.handle_tool_calls_round_fn(
        request=build_tool_round_request(
            db=request.db,
            messages=request.messages,
            state=request.state,
            runtime=request.runtime,
            step_number=request.step_number,
            step_context=request.step_context,
            round_result=request.round_result,
        ),
    )
    return None


async def _discard_streamed_tool_round_content(request: AgentRoundOutcomeRequest) -> None:
    """工具决策前的正文只是过程性话术，工具调用成立后精确撤回。"""

    if request.round_result.output_deferred or not request.round_result.content_buf:
        return
    discard = getattr(request.runtime.emitter, "content_block_discarded", None)
    if discard is None:
        return
    await discard(block_id=request.step_context.text_block_id)
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        lifecycle.record_output(disposition="suppressed", source="none", reason="tool_retracted")


async def _complete_content_filtered_round(request: AgentRoundOutcomeRequest) -> None:
    """服务商审核拦截了这一轮：整条回复熔断为固定提示，不再请求模型，也不留工具记录供后续回放。"""

    request.runtime.warning_fn(
        "模型服务商内容审核拦截，整条回复替换为固定提示: "
        f"conv_id={request.runtime.conversation_id} run_id={request.runtime.run_id} "
        f"step={request.step_number} model_id={request.runtime.model_id}"
    )
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        lifecycle.record_output(disposition="replaced", source="server", reason="content_filtered")
    await complete_text_response_step(
        context=request.step_context,
        emitter=request.runtime.emitter,
        session_cache=request.runtime.session_cache,
        complete_step_fn=request.runtime.complete_step_fn,
        completed_tool_calls=request.state.total_tool_calls,
        max_tool_calls=request.runtime.limits.max_tool_calls,
        clock=request.runtime.clock,
    )
    await replace_with_content_filtered_block(
        content_blocks=request.state.content_blocks,
        emitter=request.runtime.emitter,
    )
    request.state.tool_transcript = []


async def _complete_unknown_round(request: AgentRoundOutcomeRequest) -> None:
    request.state.mark_unknown_terminated()
    await _complete_text_round(request)


async def _emit_final_answer_used_evidence(request: AgentRoundOutcomeRequest) -> None:
    emit = getattr(request.runtime.emitter, "evidence_item_upserted", None)
    if emit is None:
        return
    try:
        evidence_items = build_used_final_answer_evidence(
            content_blocks=request.state.content_blocks,
            answer_text=request.round_result.content_buf,
        )
        for evidence in evidence_items:
            await emit(tool_call_id=None, evidence=evidence)
    except StreamWriteTerminalError:
        raise
    except Exception as exc:  # noqa: BLE001 — 非写入故障的 used 判定不能阻断主回答完成
        request.runtime.warning_fn(f"发送最终回答 used evidence 失败: {exc}")
