"""Agent Loop 内部 update_plan 调用的处理：记录计划并回执，不拦截同轮其他工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.services.agent.plan_coordinator import PlanCoordinator

UPDATE_PLAN_TOOL_NAME = "update_plan"
PLAN_ITEM_ARGUMENT_NAME = "_plan_item_id"


@dataclass(frozen=True)
class PlanControlResult:
    external_tool_calls: list[dict]
    tool_responses: dict[str, str] = field(default_factory=dict)
    plan_item_ids: dict[str, str] = field(default_factory=dict)


def _parse_arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _response(*, status: str, reason: str, revision: int) -> str:
    return json.dumps(
        {"status": status, "reason": reason, "revision": revision},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _extract_plan_item_binding(call: dict) -> tuple[dict, str | None]:
    """提取并移除只供 Fusion 使用的计划项 ID，避免污染真实工具参数。"""

    raw_arguments = call.get("arguments")
    arguments = _parse_arguments(raw_arguments)
    if arguments is None or PLAN_ITEM_ARGUMENT_NAME not in arguments:
        return call, None
    requested_item_id = arguments.pop(PLAN_ITEM_ARGUMENT_NAME)
    cleaned = dict(call)
    cleaned["arguments"] = (
        json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        if isinstance(raw_arguments, str)
        else arguments
    )
    return cleaned, requested_item_id if isinstance(requested_item_id, str) else None


async def process_plan_control_calls(
    *,
    tool_calls: list[dict],
    coordinator: PlanCoordinator,
    emitter: Any,
) -> PlanControlResult:
    """先记录 update_plan，再把同轮外部调用原样放行，只尽力对应到计划步骤用于展示。"""

    responses: dict[str, str] = {}
    external_calls: list[dict] = []
    requested_item_ids: list[str | None] = []
    for call in tool_calls:
        if call.get("name") != UPDATE_PLAN_TOOL_NAME:
            prepared_call, requested_item_id = _extract_plan_item_binding(call)
            external_calls.append(prepared_call)
            requested_item_ids.append(requested_item_id)
            continue
        result = coordinator.apply_model_update(_parse_arguments(call.get("arguments")))
        responses[str(call.get("id", ""))] = _response(
            status="accepted" if result.accepted else "rejected",
            reason=result.reason,
            revision=coordinator.revision,
        )
        if result.snapshot is not None:
            await emitter.plan_snapshot(**result.snapshot)

    mapped_item_ids = coordinator.plan_item_ids_for_tools(
        [str(call.get("name", "")) for call in external_calls],
        requested_item_ids=requested_item_ids,
    )
    plan_item_ids: dict[str, str] = {}
    prepared: list[dict] = []
    for call, plan_item_id in zip(external_calls, mapped_item_ids):
        if plan_item_id is None:
            prepared.append(call)
            continue
        plan_item_ids[str(call.get("id", ""))] = plan_item_id
        prepared.append({**call, "plan_item_id": plan_item_id})
    return PlanControlResult(
        external_tool_calls=prepared,
        tool_responses=responses,
        plan_item_ids=plan_item_ids,
    )
