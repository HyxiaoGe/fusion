"""产品工具（地图、天气、出行）的结构化结果钩子。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.services.stream.itinerary_observability import build_itinerary_run_payload, emit_itinerary_run_log
from app.services.stream.place_choice import pending_place_choice
from app.services.stream.product_tool_round import record_itinerary_tool_observations
from app.services.stream.structured_answer import StructuredAnswers, ToolRoundDecision
from app.utils.user_visible_content import sanitize_internal_tool_names

if TYPE_CHECKING:
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord
    from app.services.stream.tool_round import ToolRoundOutcome


class ProductStructuredAnswers(StructuredAnswers):
    def pending_user_choice(self, result: Any) -> dict[str, Any] | None:
        # 地名歧义失败且带候选时等用户选择。
        return pending_place_choice(getattr(result, "status", None), getattr(result, "data", None))

    def record_tool_results(self, state: AgentLoopState, results: list[ToolExecutionRecord]) -> None:
        record_itinerary_tool_observations(state, results)

    def rewrite_answer(self, answer: str, state: AgentLoopState) -> str:
        # 只去掉漏出的内部工具标识，品牌、实体和来源原文保留。
        return sanitize_internal_tool_names(answer, final=True)

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
        stats = state.run_stats(run_id)
        emit_itinerary_run_log(
            build_itinerary_run_payload(
                run_id=run_id,
                model_id=model_id,
                provider=provider,
                content_blocks=state.content_blocks,
                tool_observations=state.itinerary_tool_observations,
                run_status=run_status,
                finish_reason=finish_reason,
                limit_reason=limit_reason,
                total_duration_ms=duration_ms_factory(),
                total_steps=stats.total_steps,
                total_tool_calls=stats.total_tool_calls,
            )
        )

    def after_tool_round(
        self,
        state: AgentLoopState,
        *,
        outcome: ToolRoundOutcome | None,
    ) -> ToolRoundDecision:
        if outcome is not None and outcome.rich_result_count > 0:
            return ToolRoundDecision.CONTINUE
        return ToolRoundDecision.DEFAULT


PRODUCT_STRUCTURED_ANSWERS = ProductStructuredAnswers()
