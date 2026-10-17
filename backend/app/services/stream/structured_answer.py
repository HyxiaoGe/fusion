"""服务端组织的结构化结果与回答：工具给出结构化结果或待用户补充的信息时，不依赖模型正文也能收口。

loop 核心只在固定位置调用这些钩子：工具结果如何记录、本轮要追加或撤回哪些汇总结果块、
能否不再调模型直接收口、模型空回答时补什么、触顶时交付什么、交付前如何改写，以及工具轮
之后是否继续。基类不提供任何结构化结果；聊天入口装产品实现（地图、天气、出行），脱离聊天
的调用方按需选择。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.prompts.prompt_message import PromptMessage
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord
    from app.services.stream.tool_round import ToolRoundOutcome


class ToolRoundDecision(Enum):
    # 交给后续通用判断（如连续搜索无进展时转总结）。
    DEFAULT = "default"
    # 本轮拿到了新的结构化结果，直接进入下一轮。
    CONTINUE = "continue"
    # 已有可直接交付的结构化回答（或必须等用户补充），不再调用模型。
    READY = "ready"


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

    def has_answer(self, state: AgentLoopState) -> bool:
        return False

    def compose_answer(self, state: AgentLoopState, messages: list[PromptMessage]) -> str:
        """模型正文为空时补一段；返回空串表示没有可补的内容。"""

        return ""

    def compose_terminal_answer(self, state: AgentLoopState, messages: list[PromptMessage]) -> str:
        """触顶或直接收口时只交付已知事实。"""

        return ""

    def rewrite_answer(self, answer: str, state: AgentLoopState) -> str:
        return answer

    def after_tool_round(
        self,
        state: AgentLoopState,
        *,
        tool_calls: list[dict],
        outcome: ToolRoundOutcome | None,
    ) -> ToolRoundDecision:
        return ToolRoundDecision.DEFAULT


NO_STRUCTURED_ANSWERS = StructuredAnswers()
