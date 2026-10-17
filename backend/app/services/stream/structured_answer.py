"""服务端组织的结构化结果：工具结果之外的汇总结果块与交付前的净化。

loop 核心只在固定位置调用这些钩子：工具结果如何记录、本轮要追加或撤回哪些汇总结果块、
交付前如何净化，以及工具轮之后是否继续。回答始终由模型写：空回答、触顶和等用户补充都走
通用的模型收尾，服务端不按结构化结果拼回答。基类不提供任何结构化结果；聊天入口装产品实现
（地图、天气、出行），脱离聊天的调用方按需选择。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord
    from app.services.stream.tool_round import ToolRoundOutcome


class ToolRoundDecision(Enum):
    # 交给后续通用判断（如连续搜索无进展时转总结）。
    DEFAULT = "default"
    # 本轮拿到了新的结构化结果，直接进入下一轮。
    CONTINUE = "continue"


@dataclass(frozen=True)
class StructuredRoundUpdate:
    """工具轮汇总出的结果块变化；核心在 checkpoint 与本轮结果块推送之后先撤回、再推送。"""

    discarded_block_ids: tuple[str, ...] = ()
    # (事件 tool_call_id, content block)
    upserted_blocks: tuple[tuple[str, Any], ...] = ()


class StructuredAnswers:
    def pending_user_choice(self, result: Any) -> dict[str, Any] | None:
        """工具结果需要用户在候选中选择时返回候选；这类结果不算工具故障。"""

        return None

    def record_tool_results(self, state: AgentLoopState, results: list[ToolExecutionRecord]) -> None:
        return None

    def update_round_results(
        self,
        state: AgentLoopState | None,
        content_blocks: list[Any],
        results: list[ToolExecutionRecord],
        *,
        built_content_blocks: dict[str, Any],
    ) -> StructuredRoundUpdate:
        """在 checkpoint 之前改写 content_blocks（如汇总行程），返回需要推送的变化。"""

        return StructuredRoundUpdate()

    def rewrite_answer(self, answer: str, state: AgentLoopState) -> str:
        return answer

    def observe_run_finished(
        self,
        state: AgentLoopState,
        *,
        run_id: str,
        model_id: str,
        provider: str,
        run_status: str,
        finish_reason: str,
        limit_reason: str | None,
        duration_ms_factory: Callable[[], int],
    ) -> None:
        """run 终态后的产品观测；核心吞掉这里的异常，观测失败不能改变终态。"""

        return None

    def after_tool_round(
        self,
        state: AgentLoopState,
        *,
        outcome: ToolRoundOutcome | None,
    ) -> ToolRoundDecision:
        return ToolRoundDecision.DEFAULT


NO_STRUCTURED_ANSWERS = StructuredAnswers()
