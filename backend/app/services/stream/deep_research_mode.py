"""深度研究模式：按研究阶段锁定工具、注入证据工作集、校验成文完成度。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.prompts.agent_loop import RESEARCH_EVIDENCE_SUMMARY_PROMPT
from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages
from app.ai.prompts.section_ids import (
    DEEP_RESEARCH_CONTROL_SECTION_IDS,
    RESEARCH_COMPLETION_REPAIR,
    RESEARCH_EVIDENCE_SUMMARY,
)
from app.core.logger import app_logger as logger
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.agent_loop_outcome import AgentLoopExit, AgentLoopOutcome
from app.services.stream.limit_summary import (
    LimitSummaryRoundResult,
    LimitSummaryStepRequest,
    _finish_summary_round_lifecycle,
    _record_summary_output,
    append_summary_content_blocks,
)
from app.services.stream.plan_control import UPDATE_PLAN_TOOL_NAME
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    build_research_repair_prompt,
    deep_research_stage_tool_names,
    resolve_deep_research_stage,
    validate_research_completion,
)
from app.services.stream.research_workset_messages import messages_with_research_workset
from app.services.stream.round_completion import complete_text_response_step
from app.services.stream.run_mode import RoundPreparation, RunMode, SummaryRepair
from app.services.stream_state_service import StreamWriteTerminalError

if TYPE_CHECKING:
    from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_round import ToolRoundOutcome


DEEP_RESEARCH_INCOMPLETE_TEXT = (
    "本次研究尚未完成，当前取得的可核验依据不足，暂时无法给出可靠结论。你可以稍后重试，或缩小研究范围后重新发起。"
)


class DeepResearchRunMode(RunMode):
    # 完成度检查可能丢弃本轮回答，回答先不推给用户。
    defer_round_output = True
    # 查证总结要保留已格式化的读页正文，并保持 assistant/tool 消息成对，因此去掉全部工具事务。
    summary_strips_tool_transactions = True
    summary_control_section_ids = DEEP_RESEARCH_CONTROL_SECTION_IDS

    def prepare_round(self, call_kwargs: dict, *, state: AgentLoopState, provider: str) -> RoundPreparation:
        stage = resolve_deep_research_stage(state.research_workset)
        allowed_tool_names = deep_research_stage_tool_names(stage)
        if allowed_tool_names and not state.research_plan_update_requires_evidence:
            # 取证期间可更新展示进度；只更新计划后，下一轮暂时只开放取证工具。
            allowed_tool_names = allowed_tool_names | {UPDATE_PLAN_TOOL_NAME}
        call_kwargs = _require_tool_call(
            _filter_tools_for_stage(call_kwargs, allowed_tool_names=allowed_tool_names),
            provider=provider,
        )
        allow_plan_update = any(
            tool.get("function", {}).get("name") == UPDATE_PLAN_TOOL_NAME for tool in call_kwargs.get("tools", [])
        )
        return RoundPreparation(call_kwargs=call_kwargs, stage=stage, allow_plan_update=allow_plan_update)

    def round_messages(
        self,
        messages: list[PromptMessage | dict],
        *,
        state: AgentLoopState,
        preparation: RoundPreparation,
    ) -> list[PromptMessage]:
        return messages_with_research_workset(
            messages,
            workset=state.research_workset,
            deep_research=True,
            research_stage=preparation.stage,
            allow_plan_update=preparation.allow_plan_update,
        )

    def summary_messages(self, messages: list[PromptMessage | dict], *, state: AgentLoopState) -> list[PromptMessage]:
        return messages_with_research_workset(
            messages,
            workset=state.research_workset,
            deep_research=True,
            include_candidates=False,
            terminal_summary=True,
        )

    async def intercept_round(self, request: AgentRoundOutcomeRequest) -> AgentLoopOutcome | None:
        """零公告工具的深研综合轮若仍吐出工具协议，直接进入无工具安全收口。"""

        workset = request.state.research_workset
        if not (
            workset.successful_searches >= 1
            and len(workset.successful_read_urls) >= 2
            and request.round_result.announced_tool_names == frozenset()
            and (bool(request.round_result.tool_calls) or request.round_result.finish_reason == "tool_protocol_error")
        ):
            return None
        _suppress_output(request)
        request.runtime.warning_fn(
            "深度研究综合阶段返回未公告工具协议，切换到无工具证据收口: "
            f"conv_id={request.runtime.conversation_id}, run_id={request.runtime.run_id}, "
            f"step={request.step_number}"
        )
        await _complete_step(request)
        return AgentLoopOutcome(
            exit=AgentLoopExit.SUMMARY_REQUIRED,
            summary_finish_reason="research_evidence_repair_exhausted",
        )

    def needs_answer_repair(self, request: AgentRoundOutcomeRequest) -> bool:
        if not request.state.research_network_required:
            return False
        result = validate_research_completion(
            request.state.research_workset,
            request.round_result.content_buf,
        )
        return not result.is_valid

    async def repair_answer(self, request: AgentRoundOutcomeRequest) -> AgentLoopOutcome | None:
        _suppress_output(request)
        result = validate_research_completion(
            request.state.research_workset,
            request.round_result.content_buf,
        )
        await _complete_step(request)
        _replace_system_message(
            request.messages,
            section_id=RESEARCH_COMPLETION_REPAIR,
            content=build_research_repair_prompt(result.reason, request.state.research_workset),
        )
        if request.state.record_research_repair():
            return AgentLoopOutcome(
                exit=AgentLoopExit.SUMMARY_REQUIRED,
                summary_finish_reason="research_evidence_repair_exhausted",
            )
        return None

    def after_tool_round(self, request: AgentRoundOutcomeRequest, outcome: ToolRoundOutcome) -> None:
        if outcome.tool_call_count > 0:
            request.state.research_plan_update_requires_evidence = False
        elif any(call.get("name") == UPDATE_PLAN_TOOL_NAME for call in request.round_result.tool_calls):
            # 计划只用于展示；未实际执行取证时，下一轮不能继续靠更新计划空转。
            request.state.research_plan_update_requires_evidence = True

    def extend_summary_prompt(self, prompt: str, *, section_id: str) -> str:
        if section_id == RESEARCH_EVIDENCE_SUMMARY:
            return prompt
        return f"{prompt}\n\n{RESEARCH_EVIDENCE_SUMMARY_PROMPT}"

    def summary_repair(
        self,
        request: LimitSummaryStepRequest,
        result: LimitSummaryRoundResult,
    ) -> SummaryRepair | None:
        """证据充足但最终引用缺失或越界时，允许一次无工具引用修正。"""

        workset = request.research_workset or ResearchEvidenceWorkset()
        validation = validate_research_completion(workset, result.content_buf)
        if validation.is_valid or validation.reason not in {"missing_citation", "invalid_citation"}:
            return None
        warning = request.warning_fn if request.warning_fn is not None else logger.warning
        warning(
            "深度研究收尾引用校验未通过，执行一次无工具引用修正: "
            f"conv_id={request.conversation_id}, run_id={request.run_id}, "
            f"step={request.step_number}, reason={validation.reason}"
        )
        return SummaryRepair(
            prompt=build_research_repair_prompt(validation.reason, workset),
            section_id=RESEARCH_COMPLETION_REPAIR,
            suppress_reason="research_guard",
        )

    async def commit_summary(
        self,
        request: LimitSummaryStepRequest,
        *,
        round_result: LimitSummaryRoundResult,
        text_block_id: str,
        step_id: str,
    ) -> bool | None:
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
        await request.output.append(
            "answering",
            answer,
            text_block_id,
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
        await _emit_summary_used_evidence(request=request, answer_text=answer, workset=workset)
        return False


DEEP_RESEARCH_RUN_MODE = DeepResearchRunMode()


async def _emit_summary_used_evidence(
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


def _suppress_output(request: AgentRoundOutcomeRequest) -> None:
    lifecycle = request.round_result.llm_lifecycle
    if lifecycle is not None:
        lifecycle.suppress_output("research_guard")


async def _complete_step(request: AgentRoundOutcomeRequest) -> None:
    await complete_text_response_step(
        context=request.step_context,
        emitter=request.runtime.emitter,
        session_cache=request.runtime.session_cache,
        complete_step_fn=request.runtime.complete_step_fn,
        completed_tool_calls=request.state.total_tool_calls,
        max_tool_calls=request.runtime.limits.max_tool_calls,
        clock=request.runtime.clock,
    )


def _replace_system_message(
    messages: list[PromptMessage | dict],
    *,
    section_id: str,
    content: str,
) -> None:
    normalized = ensure_prompt_messages(messages)
    replacement = PromptMessage(role="system", content=content, section_id=section_id)
    existing_index = next(
        (index for index, message in enumerate(normalized) if message.section_id == section_id),
        None,
    )
    if existing_index is None:
        normalized.append(replacement)
    else:
        normalized[existing_index] = replacement
        normalized = [
            message
            for index, message in enumerate(normalized)
            if message.section_id != section_id or index == existing_index
        ]
    messages[:] = normalized


def _filter_tools_for_stage(
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
    elif len(available_names) == 1:
        filtered_call_kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": available_names[0]},
        }
    else:
        filtered_call_kwargs["tool_choice"] = "required"
    return filtered_call_kwargs
