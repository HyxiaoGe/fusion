"""产品结构化结果的 loop 钩子：工具结果记录、交付前净化与工具轮之后是否继续。

loop 核心只在固定位置调用这些钩子。回答始终由模型写：空回答、触顶和等用户补充都走
通用的模型收尾，服务端不按结构化结果拼回答，也不替模型汇总结果块。基类不提供任何
结构化结果；聊天入口装产品实现（地图、天气、出行），脱离聊天的调用方按需选择。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord


class StructuredAnswers:
    def pending_user_choice(self, result: Any) -> dict[str, Any] | None:
        """工具结果需要用户在候选中选择时返回候选；这类结果不算工具故障。"""

        return None

    def record_tool_results(self, state: AgentLoopState, results: list[ToolExecutionRecord]) -> None:
        return None

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


NO_STRUCTURED_ANSWERS = StructuredAnswers()
