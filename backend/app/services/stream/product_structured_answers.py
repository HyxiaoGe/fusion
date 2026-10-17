"""产品工具（地图、天气、出行）的结构化结果钩子。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.schemas.chat import ItineraryResultsBlock
from app.services.stream.itinerary_observability import build_itinerary_run_payload, emit_itinerary_run_log
from app.services.stream.place_choice import pending_place_choice
from app.services.stream.product_tool_round import (
    build_product_tool_outcomes,
    record_itinerary_tool_observations,
    upsert_itinerary_result,
)
from app.services.stream.structured_answer import StructuredAnswers, StructuredRoundUpdate, ToolRoundDecision
from app.utils.user_visible_content import sanitize_internal_tool_names

if TYPE_CHECKING:
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord
    from app.services.stream.tool_round import ToolRoundOutcome


class ProductStructuredAnswers(StructuredAnswers):
    def pending_user_choice(self, result: Any) -> dict[str, Any] | None:
        # 地名歧义失败且带候选时等用户选择；修参通道（如天气）不走这里。
        return pending_place_choice(getattr(result, "status", None), getattr(result, "data", None))

    def record_tool_results(self, state: AgentLoopState, results: list[ToolExecutionRecord]) -> None:
        record_itinerary_tool_observations(state, results)

    def update_round_results(
        self,
        state: AgentLoopState | None,
        content_blocks: list[Any],
        results: list[ToolExecutionRecord],
        *,
        built_content_blocks: dict[str, Any],
    ) -> StructuredRoundUpdate:
        product_outcomes = build_product_tool_outcomes(results, built_content_blocks=built_content_blocks)
        if state is not None:
            state.record_product_tool_outcomes(product_outcomes)
            all_product_outcomes = state.product_tool_outcomes
        else:
            all_product_outcomes = list(product_outcomes)
        previous_itinerary_id = _itinerary_block_id(content_blocks)
        itinerary_result = upsert_itinerary_result(content_blocks, product_outcomes=all_product_outcomes)
        current_itinerary_id = _itinerary_block_id(content_blocks)
        # 行程身份变化时先撤回直播页旧块，确保流式与刷新恢复一致。
        discarded = (
            (previous_itinerary_id,)
            if previous_itinerary_id is not None and previous_itinerary_id != current_itinerary_id
            else ()
        )
        upserted = ((f"itinerary:{itinerary_result.id}", itinerary_result),) if itinerary_result is not None else ()
        return StructuredRoundUpdate(discarded_block_ids=discarded, upserted_blocks=upserted)

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


def _itinerary_block_id(content_blocks: list[Any]) -> str | None:
    return next((block.id for block in content_blocks if isinstance(block, ItineraryResultsBlock)), None)
