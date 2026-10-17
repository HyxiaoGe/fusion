"""产品工具（地图、天气、出行）的结构化回答。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.schemas.chat import ItineraryResultsBlock
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_TOOL_NAMES
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_TOOL_NAMES
from app.services.stream.place_choice import pending_place_choice
from app.services.stream.product_result_answer import (
    build_grounded_product_answer,
    build_place_choice_clarification,
    build_product_tool_failure_answer,
    build_tool_repair_clarification,
    has_product_result_blocks,
    neutralize_product_provider_mentions,
)
from app.services.stream.product_tool_round import (
    build_product_tool_outcomes,
    record_itinerary_tool_observations,
    record_tool_repairs,
    upsert_itinerary_result,
)
from app.services.stream.structured_answer import StructuredAnswers, StructuredRoundUpdate, ToolRoundDecision
from app.services.weather import WEATHER_TOOL_NAMES

if TYPE_CHECKING:
    from app.ai.prompts.prompt_message import PromptMessage
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.tool_execution_result import ToolExecutionRecord
    from app.services.stream.tool_round import ToolRoundOutcome

PRODUCT_TOOL_NAMES = AMAP_PRODUCT_TOOL_NAMES | WEATHER_TOOL_NAMES | FLYAI_TRAVEL_TOOL_NAMES


class ProductStructuredAnswers(StructuredAnswers):
    def pending_user_choice(self, result: Any) -> dict[str, Any] | None:
        # 地名歧义失败且带候选时等用户选择；修参通道（如天气）不走这里。
        return pending_place_choice(getattr(result, "status", None), getattr(result, "data", None))

    def record_tool_results(self, state: AgentLoopState, results: list[ToolExecutionRecord]) -> None:
        record_tool_repairs(state, results)
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

    def has_answer(self, state: AgentLoopState) -> bool:
        return (
            has_product_result_blocks(state.content_blocks)
            or state.product_tool_attempted
            or bool(state.pending_tool_repairs)
        )

    def compose_answer(self, state: AgentLoopState, messages: list[PromptMessage]) -> str:
        # 空正文是格式问题：用已取得的结构化结果和待用户确认的信息补一段，不覆盖模型写出的内容。
        answer = "\n\n".join(
            part
            for part in (
                build_grounded_product_answer(state.content_blocks, messages=messages),
                _build_user_clarification(state),
            )
            if part
        )
        if not answer and state.product_tool_attempted:
            answer = build_product_tool_failure_answer(messages)
        return answer

    def compose_terminal_answer(self, state: AgentLoopState, messages: list[PromptMessage]) -> str:
        answer = build_grounded_product_answer(state.content_blocks, messages=messages)
        if not answer:
            answer = build_product_tool_failure_answer(messages)
        return "\n\n".join(part for part in (answer, _build_user_clarification(state)) if part)

    def rewrite_answer(self, answer: str, state: AgentLoopState) -> str:
        return neutralize_product_provider_mentions(answer, state.content_blocks)

    def after_tool_round(
        self,
        state: AgentLoopState,
        *,
        tool_calls: list[dict],
        outcome: ToolRoundOutcome | None,
    ) -> ToolRoundDecision:
        if outcome is not None:
            state.record_product_tool_attempt(
                outcome.tool_call_count > 0
                and any(str(tool_call.get("name", "")) in PRODUCT_TOOL_NAMES for tool_call in tool_calls)
            )
        if _requires_user_input(state):
            return ToolRoundDecision.READY
        if outcome is not None and outcome.product_result_count > 0:
            return ToolRoundDecision.CONTINUE
        return ToolRoundDecision.DEFAULT


PRODUCT_STRUCTURED_ANSWERS = ProductStructuredAnswers()


def _build_user_clarification(state: AgentLoopState) -> str:
    # 同一轮已有其他产品结果时按正常校验交付模型回答，不用地名确认覆盖已查到的内容。
    place_choice = (
        ""
        if has_product_result_blocks(state.content_blocks)
        else build_place_choice_clarification(state.pending_place_choices)
    )
    parts = (build_tool_repair_clarification(state.pending_tool_repairs), place_choice)
    return "\n\n".join(part for part in parts if part)


def _itinerary_block_id(content_blocks: list[Any]) -> str | None:
    return next((block.id for block in content_blocks if isinstance(block, ItineraryResultsBlock)), None)


def _requires_user_input(state: AgentLoopState) -> bool:
    return any(
        isinstance(repair, dict) and repair.get("requires_user_input") is True
        for repair in state.pending_tool_repairs.values()
    )
