"""Agent loop 状态机 driver。"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import replace
from inspect import Parameter, signature

from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages
from app.ai.prompts.section_ids import (
    DEEP_RESEARCH_STAGE,
    RESEARCH_EVIDENCE_WORKSET,
)
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
from app.services.stream.plan_control import UPDATE_PLAN_TOOL_NAME
from app.services.stream.product_result_answer import has_product_result_blocks
from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs
from app.services.stream.research_evidence import (
    build_deep_research_stage_prompt,
    build_research_untrusted_context_messages,
    build_research_workset_prompt,
    count_unattempted_requested_urls,
    deep_research_stage_tool_names,
    resolve_deep_research_stage,
)
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_ban import forbid_tool_calls, ignored_tool_ban, without_tool_definitions


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


def _accepts_keyword(fn, keyword: str) -> bool:
    try:
        parameters = signature(fn).parameters
    except (TypeError, ValueError):
        return True

    return keyword in parameters or any(parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values())


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
    research_stage = None
    allow_plan_update = False
    if runtime.task_mode == "deep_research":
        research_stage = resolve_deep_research_stage(state.research_workset)
        allowed_tool_names = deep_research_stage_tool_names(research_stage)
        if allowed_tool_names and not state.research_plan_update_requires_evidence:
            # 取证期间可更新展示进度；只更新计划后，下一轮暂时只开放取证工具。
            allowed_tool_names = allowed_tool_names | {UPDATE_PLAN_TOOL_NAME}
        call_kwargs = _require_tool_call(
            _filter_tools_for_research_stage(call_kwargs, allowed_tool_names=allowed_tool_names),
            provider=runtime.provider,
        )
        allow_plan_update = any(
            tool.get("function", {}).get("name") == UPDATE_PLAN_TOOL_NAME for tool in call_kwargs.get("tools", [])
        )
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
    effective_messages = _messages_with_research_workset(
        messages,
        state=state,
        runtime=runtime,
        research_stage=research_stage,
        allow_plan_update=allow_plan_update,
    )
    run_round_kwargs = dict(
        conversation_id=runtime.conversation_id,
        task_id=runtime.task_id,
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
    should_defer_output = (
        has_product_result_blocks(state.content_blocks)
        or state.product_tool_attempted
        or bool(state.tool_issue_names)
        or state.pending_tool_repairs
        or runtime.task_mode == "deep_research"
    )
    if should_defer_output and _accepts_keyword(runtime.run_round_fn, "defer_output"):
        run_round_kwargs["defer_output"] = True
    if runtime.llm_round_detail_scheduler is not None and _accepts_keyword(
        runtime.run_round_fn,
        "llm_round_detail_scheduler",
    ):
        run_round_kwargs["llm_round_detail_scheduler"] = runtime.llm_round_detail_scheduler
    if _accepts_keyword(runtime.run_round_fn, "on_context_trimmed"):
        run_round_kwargs["on_context_trimmed"] = state.record_context_plan
    if runtime.output_tool_names and _accepts_keyword(runtime.run_round_fn, "draft_tool_names"):
        run_round_kwargs["draft_tool_names"] = runtime.output_tool_names
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


async def _discard_round(round_result: AgentRoundResult) -> None:
    lifecycle = round_result.llm_lifecycle
    if lifecycle is None:
        return
    lifecycle.suppress_output("tool_round")
    await lifecycle.finish_success(output_visible=False)


def _filter_tools_for_research_stage(
    call_kwargs: dict,
    *,
    allowed_tool_names: frozenset[str] | None,
) -> dict:
    if allowed_tool_names is None or not call_kwargs.get("tools"):
        return call_kwargs

    filtered_tools = []
    for tool in call_kwargs["tools"]:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = function.get("name") if isinstance(function, dict) else None
        if tool_name in allowed_tool_names:
            filtered_tools.append(tool)

    filtered_call_kwargs = dict(call_kwargs)
    if filtered_tools:
        filtered_call_kwargs["tools"] = filtered_tools
    else:
        filtered_call_kwargs.pop("tools", None)
        filtered_call_kwargs.pop("tool_choice", None)
    return filtered_call_kwargs


def _require_tool_call(
    call_kwargs: dict,
    *,
    preferred_tool_name: str | None = None,
    provider: str | None = None,
) -> dict:
    """计划与执行阶段由服务端锁定工具选择，禁止模型用正文绕过状态机。"""

    tools = call_kwargs.get("tools")
    if not isinstance(tools, list) or not tools:
        filtered_call_kwargs = dict(call_kwargs)
        filtered_call_kwargs.pop("tool_choice", None)
        return filtered_call_kwargs

    available_names = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            available_names.append(name)

    filtered_call_kwargs = dict(call_kwargs)
    if provider == "moonshot":
        filtered_call_kwargs["tool_choice"] = "required"
    elif preferred_tool_name in available_names:
        filtered_call_kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": preferred_tool_name},
        }
    elif len(available_names) == 1:
        filtered_call_kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": available_names[0]},
        }
    else:
        filtered_call_kwargs["tool_choice"] = "required"
    return filtered_call_kwargs


async def _filter_exhausted_dynamic_tools(
    *,
    call_kwargs: dict,
    dynamic_tool_handlers: dict[str, object],
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
    dynamic_tool_handlers: dict[str, object] | None,
) -> set[str]:
    if not dynamic_tool_handlers:
        return set()
    exhausted_aliases: set[str] = set()
    for alias, handler in dynamic_tool_handlers.items():
        is_exhausted = getattr(handler, "is_run_budget_exhausted", None)
        if is_exhausted is not None and await is_exhausted():
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
            messages=deepcopy(
                _messages_with_research_workset(
                    messages,
                    state=state,
                    runtime=runtime,
                    include_candidates=runtime.task_mode != "deep_research",
                    terminal_summary=True,
                )
            ),
            summary_finish_reason=summary_finish_reason,
            document_delivered=state.has_document_block(),
        ),
    )
    state.update_usage(summary_outcome.accumulated_usage)
    state.update_context(summary_outcome.context)
    if summary_outcome.incomplete and state.limit_reason is None:
        state.mark_unknown_terminated()
    return None


def _messages_with_research_workset(
    messages: list[PromptMessage | dict],
    *,
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
    include_candidates: bool = True,
    research_stage: str | None = None,
    allow_plan_update: bool = False,
    terminal_summary: bool = False,
) -> list[PromptMessage]:
    normalized = ensure_prompt_messages(messages)
    if runtime.task_mode != "deep_research":
        if not terminal_summary:
            return normalized
        # 普通请求此前只注入来源正文，不注入读取状态清单，模型的上下文里没有任何一处
        # 说明「本轮实际读到了什么」。它只能从散落在各轮的工具结果自己拼，拼不出来就
        # 填一段合理的叙述——#127 的三个确证样本都是这个形态（声称读了从未发起的读页）。
        # 这里把已有的状态清单一并注入：read_success / read_failed / candidate 逐条列出。
        workset_prompt = build_research_workset_prompt(
            state.research_workset,
            include_candidates=True,
            unattempted_request_count=count_unattempted_requested_urls(
                state.research_workset,
                _requested_urls_from_messages(normalized),
            ),
        )
        untrusted_messages = build_research_untrusted_context_messages(
            state.research_workset,
            include_candidates=True,
        )
        if not workset_prompt and not untrusted_messages:
            return normalized
        normalized = [message for message in normalized if message.section_id != RESEARCH_EVIDENCE_WORKSET]
        insert_at = 0
        while insert_at < len(normalized) and normalized[insert_at].role == "system":
            insert_at += 1
        return [
            *normalized[:insert_at],
            *(
                [PromptMessage(role="system", content=workset_prompt, section_id=RESEARCH_EVIDENCE_WORKSET)]
                if workset_prompt
                else []
            ),
            *untrusted_messages,
            *normalized[insert_at:],
        ]
    stage_prompt = (
        build_deep_research_stage_prompt(research_stage, allow_plan_update=allow_plan_update) if research_stage else ""
    )
    prompt = build_research_workset_prompt(
        state.research_workset,
        include_candidates=include_candidates,
    )
    untrusted_messages = build_research_untrusted_context_messages(
        state.research_workset,
        include_candidates=include_candidates,
    )
    if not stage_prompt and not prompt and not untrusted_messages:
        return normalized
    normalized = [
        message for message in normalized if message.section_id not in {DEEP_RESEARCH_STAGE, RESEARCH_EVIDENCE_WORKSET}
    ]
    insert_at = 0
    while insert_at < len(normalized) and normalized[insert_at].role == "system":
        insert_at += 1
    return [
        *normalized[:insert_at],
        *([PromptMessage(role="system", content=stage_prompt, section_id=DEEP_RESEARCH_STAGE)] if stage_prompt else []),
        *([PromptMessage(role="system", content=prompt, section_id=RESEARCH_EVIDENCE_WORKSET)] if prompt else []),
        *untrusted_messages,
        *normalized[insert_at:],
    ]


# 用户原文里的 URL 提取。这是词法解析，不是意图判断——与 #132 删除的选包判据性质
# 不同：判断「这句话想不想查证」是猜意图，取出「这句话里有哪些 URL」是 parsing。
_REQUEST_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _requested_urls_from_messages(messages: list[PromptMessage]) -> list[str]:
    urls: list[str] = []
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        urls.extend(_REQUEST_URL_RE.findall(str(getattr(message, "content", "") or "")))
    return urls
