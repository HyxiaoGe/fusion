"""Agent loop 状态机 driver。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from app.ai.llm_manager import llm_manager
from app.ai.prompts.prompt_message import PromptMessage
from app.services.chat.tool_transcript import without_tool_transactions
from app.services.stream.agent_loop_outcome import AgentLoopExit, AgentLoopOutcome
from app.services.stream.agent_loop_policy import check_agent_loop_limit
from app.services.stream.agent_loop_round_outcome import (
    AgentRoundOutcomeRequest,
    handle_agent_round_outcome,
)
from app.services.stream.agent_loop_runtime import AgentLoopRuntime
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_loop_step_requests import build_limit_summary_step_request
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.first_output_timeout import (
    FALLBACK_MODEL_ID,
    FIRST_OUTPUT_TIMEOUT_S,
    FirstOutputTimeoutError,
)
from app.services.stream.product_result_answer import has_product_result_blocks
from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_ban import forbid_tool_calls, ignored_tool_ban, without_tool_definitions
from app.services.tool_handlers.base import BaseToolHandler


async def run_agent_loop(
    *,
    db,
    messages: list[PromptMessage],
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
) -> AgentLoopOutcome:
    runtime = replace(runtime, complete_step_fn=state.bind_step_completion(runtime.complete_step_fn))
    while True:
        if await _stop_if_limit_reached(state=state, runtime=runtime):
            break

        step_number, step_context = await _start_next_step(state=state, runtime=runtime)
        try:
            round_result = await _run_round(
                messages=messages,
                state=state,
                runtime=runtime,
                step_number=step_number,
                step_context=step_context,
            )
        except FirstOutputTimeoutError as error:
            runtime = _switch_to_fallback_model(runtime, state=state, error=error)
            round_result = await _run_round(
                messages=messages,
                state=state,
                runtime=runtime,
                step_number=step_number,
                step_context=step_context,
            )
        outcome = await handle_agent_round_outcome(
            request=AgentRoundOutcomeRequest(
                db=db,
                messages=messages,
                state=state,
                runtime=runtime,
                step_number=step_number,
                step_context=step_context,
                round_result=round_result,
            ),
        )
        if outcome is None:
            continue
        if outcome.exit == AgentLoopExit.SUPERSEDED:
            return outcome
        if outcome.exit == AgentLoopExit.PRODUCT_RESULT_READY:
            await _complete_product_result_without_llm(
                db=db,
                messages=messages,
                state=state,
                runtime=runtime,
            )
            break
        if outcome.exit == AgentLoopExit.SUMMARY_REQUIRED:
            state.finish_reason = outcome.summary_finish_reason or "empty_answer_summary"
            superseded = await _run_limit_summary(
                db=db,
                state=state,
                runtime=runtime,
                messages=messages,
                summary_finish_reason=outcome.summary_finish_reason or "limit_summary",
            )
            if superseded is not None:
                return superseded
            break
        break

    if state.limit_reason is not None:
        if (
            has_product_result_blocks(state.content_blocks)
            or state.product_tool_attempted
            or state.pending_tool_repairs
        ):
            limit_finish_reason = state.finish_reason
            try:
                await _complete_product_result_without_llm(
                    db=db,
                    messages=messages,
                    state=state,
                    runtime=runtime,
                )
            finally:
                state.finish_reason = limit_finish_reason
        else:
            superseded = await _run_limit_summary(db=db, state=state, runtime=runtime, messages=messages)
            if superseded is not None:
                return superseded

    return AgentLoopOutcome(exit=AgentLoopExit.COMPLETED)


async def _complete_product_result_without_llm(
    *,
    db,
    messages: list[PromptMessage],
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
) -> None:
    """产品结果或待修参数用结构化状态确定性收口，不再消耗模型调用。"""
    step_number, step_context = await _start_next_step(state=state, runtime=runtime)
    state.finish_reason = "stop"
    await handle_agent_round_outcome(
        request=AgentRoundOutcomeRequest(
            db=db,
            messages=messages,
            state=state,
            runtime=runtime,
            step_number=step_number,
            step_context=step_context,
            terminal=True,
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=state.accumulated_usage,
                context=state.last_context,
                output_deferred=True,
            ),
        )
    )


async def _stop_if_limit_reached(*, state: AgentLoopState, runtime: AgentLoopRuntime) -> bool:
    state.limit_reason = check_agent_loop_limit(
        elapsed_seconds=state.active_elapsed_seconds(now=runtime.clock(), run_start=runtime.run_start),
        step=state.step,
        total_tool_calls=state.total_tool_calls,
        limits=runtime.limits,
    )
    if state.limit_reason is None:
        return False

    state.finish_reason = "timeout" if state.limit_reason == "timeout" else "tool_calls"
    await runtime.emitter.run_limit_reached(reason=state.limit_reason)
    return True


async def _start_next_step(
    *,
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
) -> tuple[int, AgentStepContext]:
    step_number = state.next_step_number()
    start_step_kwargs = {
        "emitter": runtime.emitter,
        "session_cache": runtime.session_cache,
        "run_id": runtime.run_id,
        "step_number": step_number,
        "completed_tool_calls": state.total_tool_calls,
        "max_tool_calls": runtime.limits.max_tool_calls,
        "clock": runtime.clock,
        "on_step_started": state.mark_current_step,
    }
    step_context = await runtime.start_step_fn(**start_step_kwargs)
    state.mark_current_step(step_context.step_id)
    return step_number, step_context


async def _run_round(
    *,
    messages: list[PromptMessage],
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
    step_number: int,
    step_context: AgentStepContext,
) -> AgentRoundResult:
    call_kwargs = await _filter_exhausted_dynamic_tools(
        call_kwargs=runtime.call_kwargs,
        dynamic_tool_handlers=runtime.dynamic_tool_handlers,
    )
    preparation = runtime.mode.prepare_round(call_kwargs, state=state, provider=runtime.provider)
    call_kwargs = preparation.call_kwargs
    # 本轮不开放工具（深研成文、动态工具耗尽）时仍公告 run 的工具定义并禁止调用，
    # 历史工具事务才能原样留在上下文里，见 tool_ban。
    run_tools = list(runtime.call_kwargs.get("tools") or [])
    tools_forbidden = bool(run_tools) and not call_kwargs.get("tools")
    if tools_forbidden:
        call_kwargs = forbid_tool_calls(call_kwargs, run_tools)
    unconfigured_call_kwargs = call_kwargs
    call_kwargs = configure_reasoning_call_kwargs(
        call_kwargs,
        provider=runtime.provider,
        should_use_reasoning=runtime.should_use_reasoning,
    )
    effective_messages = runtime.mode.round_messages(messages, state=state, preparation=preparation)
    run_round_kwargs = dict(
        output=runtime.output,
        conversation_id=runtime.conversation_id,
        run_id=runtime.run_id,
        step_number=step_number,
        model_id=runtime.model_id,
        provider=runtime.provider,
        litellm_model=runtime.litellm_model,
        litellm_kwargs=runtime.litellm_kwargs,
        messages=effective_messages,
        should_use_reasoning=runtime.should_use_reasoning,
        call_kwargs=call_kwargs,
        accumulated_usage=state.accumulated_usage,
        step_context=step_context,
        llm_call_fn=runtime.llm_call_fn,
        stream_round_fn=runtime.stream_round_fn,
        log_round_summary_fn=runtime.log_round_summary_fn,
        assistant_message_id=runtime.assistant_message_id,
        emitter=runtime.emitter,
        on_context_updated=state.update_context,
    )
    # 服务端不再改写模型回答，只有模式要求先校验（如深度研究的完成度检查）时才暂缓输出。
    if runtime.mode.defer_round_output:
        run_round_kwargs["defer_output"] = True
    if runtime.llm_round_detail_scheduler is not None:
        run_round_kwargs["llm_round_detail_scheduler"] = runtime.llm_round_detail_scheduler
    run_round_kwargs["on_context_trimmed"] = state.record_context_plan
    if runtime.output_tool_names:
        run_round_kwargs["draft_tool_names"] = runtime.output_tool_names
    if state.active_model_id is None and runtime.model_id != FALLBACK_MODEL_ID:
        run_round_kwargs["first_output_timeout_s"] = FIRST_OUTPUT_TIMEOUT_S
    round_result = await runtime.run_round_fn(**run_round_kwargs)
    if tools_forbidden and ignored_tool_ban(
        tool_calls=round_result.tool_calls,
        finish_reason=round_result.finish_reason,
    ):
        runtime.warning_fn(
            "禁止调用工具的轮次仍返回工具调用，丢弃该轮并改用无工具请求重做: "
            f"conv_id={runtime.conversation_id}, run_id={runtime.run_id}, step={step_number}, "
            f"finish_reason={round_result.finish_reason}"
        )
        await _discard_round(round_result)
        run_round_kwargs.update(
            messages=without_tool_transactions(effective_messages, state.history_tool_call_sequences),
            call_kwargs=configure_reasoning_call_kwargs(
                without_tool_definitions(unconfigured_call_kwargs),
                provider=runtime.provider,
                should_use_reasoning=runtime.should_use_reasoning,
            ),
            accumulated_usage=round_result.accumulated_usage,
        )
        round_result = await runtime.run_round_fn(**run_round_kwargs)
    state.finish_reason = round_result.finish_reason
    state.update_usage(round_result.accumulated_usage)
    state.update_context(round_result.context)
    return round_result


def _switch_to_fallback_model(
    runtime: AgentLoopRuntime,
    *,
    state: AgentLoopState,
    error: FirstOutputTimeoutError,
) -> AgentLoopRuntime:
    """首字超时后本次回答剩余轮次都改用备用模型；备用模型不可用时原模型不限时重试。"""
    try:
        litellm_model, provider, litellm_kwargs = llm_manager.resolve_model(FALLBACK_MODEL_ID)
    except ValueError:
        runtime.warning_fn(
            "首字超时但备用模型不可用，原模型不限时重试: "
            f"conv_id={runtime.conversation_id}, run_id={runtime.run_id}, model_id={runtime.model_id}"
        )
        state.active_model_id = runtime.model_id
        return runtime
    runtime.warning_fn(
        f"模型 {error.timeout_s:g}s 内无输出，本次回答改用备用模型: "
        f"conv_id={runtime.conversation_id}, run_id={runtime.run_id}, "
        f"model_id={runtime.model_id}, fallback={FALLBACK_MODEL_ID}"
    )
    state.active_model_id = FALLBACK_MODEL_ID
    return replace(
        runtime,
        model_id=FALLBACK_MODEL_ID,
        provider=provider,
        litellm_model=litellm_model,
        litellm_kwargs=dict(litellm_kwargs or {}),
    )


async def _discard_round(round_result: AgentRoundResult) -> None:
    lifecycle = round_result.llm_lifecycle
    if lifecycle is None:
        return
    lifecycle.suppress_output("tool_round")
    await lifecycle.finish_success(output_visible=False)


async def _filter_exhausted_dynamic_tools(
    *,
    call_kwargs: dict,
    dynamic_tool_handlers: dict[str, BaseToolHandler] | None,
) -> dict:
    """在下一轮模型调用前隐藏已耗尽单服务预算的动态工具。"""

    if not dynamic_tool_handlers or not call_kwargs.get("tools"):
        return call_kwargs

    exhausted_aliases = await _exhausted_dynamic_tool_aliases(dynamic_tool_handlers)
    if not exhausted_aliases:
        return call_kwargs

    filtered_tools = []
    for tool in call_kwargs["tools"]:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = function.get("name") if isinstance(function, dict) else None
        if tool_name not in exhausted_aliases:
            filtered_tools.append(tool)

    filtered_call_kwargs = dict(call_kwargs)
    if filtered_tools:
        filtered_call_kwargs["tools"] = filtered_tools
    else:
        filtered_call_kwargs.pop("tools", None)
        filtered_call_kwargs.pop("tool_choice", None)
    return filtered_call_kwargs


async def _exhausted_dynamic_tool_aliases(
    dynamic_tool_handlers: dict[str, BaseToolHandler] | None,
) -> set[str]:
    if not dynamic_tool_handlers:
        return set()
    exhausted_aliases: set[str] = set()
    for alias, handler in dynamic_tool_handlers.items():
        if await handler.is_run_budget_exhausted():
            exhausted_aliases.add(alias)
    return exhausted_aliases


async def _run_limit_summary(
    *,
    db=None,
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
    messages: list[PromptMessage],
    summary_finish_reason: str = "limit_summary",
) -> AgentLoopOutcome | None:
    """进入无工具终局总结。返回非空表示 run 已被新请求取代。"""

    summary_outcome = await runtime.run_limit_summary_step_fn(
        request=build_limit_summary_step_request(
            state=state,
            runtime=runtime,
            messages=deepcopy(runtime.mode.summary_messages(messages, state=state)),
            summary_finish_reason=summary_finish_reason,
            document_delivered=state.has_document_block(),
        ),
    )
    state.update_usage(summary_outcome.accumulated_usage)
    state.update_context(summary_outcome.context)
    if summary_outcome.incomplete and state.limit_reason is None:
        state.mark_unknown_terminated()
    return None
