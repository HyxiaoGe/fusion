"""服务端组织的回答：工具给出结构化结果或待用户补充的信息时，不依赖模型正文也能收口。

loop 核心只在固定位置调用这些钩子：能否不再调模型直接收口、模型空回答时补什么、
触顶时交付什么、交付前如何改写，以及工具轮之后是否继续。基类不提供任何结构化回答；
聊天入口装产品实现（地图、天气、出行），脱离聊天的调用方按需选择。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.prompts.prompt_message import PromptMessage
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_round import ToolRoundOutcome


class ToolRoundDecision(Enum):
    # 交给后续通用判断（如连续搜索无进展时转总结）。
    DEFAULT = "default"
    # 本轮拿到了新的结构化结果，直接进入下一轮。
    CONTINUE = "continue"
    # 已有可直接交付的结构化回答（或必须等用户补充），不再调用模型。
    READY = "ready"


class StructuredAnswers:
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
