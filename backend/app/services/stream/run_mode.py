"""Run 模式钩子：任务模式在 agent loop 核心之外的扩展点。

loop 核心只在固定位置调用这些钩子，不判断具体是哪种模式。基类即标准模式；
新模式继承 RunMode 覆写需要的钩子，并在 resolve_run_mode 里登记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages
from app.services.stream.research_workset_messages import messages_with_research_workset

if TYPE_CHECKING:
    from app.services.stream.agent_loop_outcome import AgentLoopOutcome
    from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.limit_summary import LimitSummaryRoundResult, LimitSummaryStepRequest
    from app.services.stream.tool_round import ToolRoundOutcome


@dataclass(frozen=True)
class RoundPreparation:
    """模式对本轮模型请求的约束：开放哪些工具，以及注入上下文时要用到的阶段信息。"""

    call_kwargs: dict
    stage: str | None = None
    allow_plan_update: bool = False


@dataclass(frozen=True)
class SummaryRepair:
    """收尾回答需要再修正一次时，追加给模型的系统提示。"""

    prompt: str
    section_id: str
    suppress_reason: str


class RunMode:
    # 为 True 时本轮回答先不推给用户，等 round outcome 判定可用后再提交。
    defer_round_output: bool = False
    # 收尾总结是否移除全部工具事务，以及额外移除哪些本模式的控制段。
    summary_strips_tool_transactions: bool = False
    summary_control_section_ids: frozenset[str] = frozenset()

    def prepare_round(self, call_kwargs: dict, *, state: AgentLoopState, provider: str) -> RoundPreparation:
        return RoundPreparation(call_kwargs=call_kwargs)

    def round_messages(
        self,
        messages: list[PromptMessage | dict],
        *,
        state: AgentLoopState,
        preparation: RoundPreparation,
    ) -> list[PromptMessage]:
        return ensure_prompt_messages(messages)

    def summary_messages(self, messages: list[PromptMessage | dict], *, state: AgentLoopState) -> list[PromptMessage]:
        return messages_with_research_workset(
            messages,
            workset=state.research_workset,
            deep_research=False,
            include_candidates=True,
            terminal_summary=True,
        )

    async def intercept_round(self, request: AgentRoundOutcomeRequest) -> AgentLoopOutcome | None:
        """在按 finish_reason 分发之前接管本轮；返回非空即本轮结果。"""

        return None

    def needs_answer_repair(self, request: AgentRoundOutcomeRequest) -> bool:
        return False

    async def repair_answer(self, request: AgentRoundOutcomeRequest) -> AgentLoopOutcome | None:
        """needs_answer_repair 为真时调用；返回 None 表示继续下一轮。"""

        return None

    def after_tool_round(self, request: AgentRoundOutcomeRequest, outcome: ToolRoundOutcome) -> None:
        return None

    def extend_summary_prompt(self, prompt: str, *, section_id: str) -> str:
        return prompt

    def summary_repair(
        self,
        request: LimitSummaryStepRequest,
        result: LimitSummaryRoundResult,
    ) -> SummaryRepair | None:
        return None

    async def commit_summary(
        self,
        request: LimitSummaryStepRequest,
        *,
        round_result: LimitSummaryRoundResult,
        text_block_id: str,
        step_id: str,
    ) -> bool | None:
        """由模式自行提交收尾回答时返回是否未完成；返回 None 走标准提交。"""

        return None


STANDARD_RUN_MODE = RunMode()


def resolve_run_mode(task_mode: str) -> RunMode:
    if task_mode == "deep_research":
        from app.services.stream.deep_research_mode import DEEP_RESEARCH_RUN_MODE

        return DEEP_RESEARCH_RUN_MODE
    return STANDARD_RUN_MODE
