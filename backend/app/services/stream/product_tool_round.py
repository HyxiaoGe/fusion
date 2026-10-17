"""产品工具（出行、天气、路线）在工具轮之后的观测记录。"""

from __future__ import annotations

from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.itinerary_observability import build_itinerary_tool_observation
from app.services.stream.tool_execution_result import ToolExecutionRecord


def record_itinerary_tool_observations(
    state: AgentLoopState,
    results: list[ToolExecutionRecord],
) -> None:
    observations = [
        observation for record in results if (observation := build_itinerary_tool_observation(record)) is not None
    ]
    state.record_itinerary_tool_observations(observations)
