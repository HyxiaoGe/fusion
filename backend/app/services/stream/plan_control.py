"""Agent Loop 内部计划控制调用的解析、门禁与安全回执。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.services.agent.plan_coordinator import PlanCoordinator

UPDATE_PLAN_TOOL_NAME = "update_plan"
PLAN_ITEM_ARGUMENT_NAME = "_plan_item_id"
_STATUS_DRIFT_REPAIR_REASONS = frozenset(
    {
        "multiple_running_items",
        "terminal_status_regression",
    }
)


@dataclass(frozen=True)
class PlanControlResult:
    external_tool_calls: list[dict]
    tool_responses: dict[str, str] = field(default_factory=dict)
    plan_item_ids: dict[str, str] = field(default_factory=dict)
    repair_exhausted: bool = False
    repair_attempt_count: int = 0
    repair_attempt_limit: int = 0


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


def _response(
    *,
    status: str,
    reason: str,
    revision: int,
    hint: str | None = None,
    canonical_plan: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        "status": status,
        "reason": reason,
        "revision": revision,
    }
    if hint:
        payload["hint"] = hint
    if canonical_plan:
        payload["canonical_plan"] = canonical_plan
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _control_rejection_hint(reason: str, coordinator: PlanCoordinator) -> str | None:
    if reason in {"attempted_item_removed", "terminal_item_removed"}:
        return render_runtime_prompt("stream.plan_hint_preserve")
    if reason == "missing_required_initial_tool_coverage":
        requirements = ", ".join(
            f"{tool_name} x {count}" for tool_name, count in coordinator.required_initial_tool_counts.items()
        )
        return render_runtime_prompt("stream.plan_hint_coverage", requirements=requirements)
    if reason == "research_read_missing_search_dependency":
        return render_runtime_prompt("stream.plan_hint_search_dependency")
    if reason == "missing_required_recovery_owner":
        return render_runtime_prompt("stream.plan_hint_recovery")
    if reason == "invalid_plan_structure":
        return render_runtime_prompt("stream.plan_hint_structure")
    if reason == "multiple_tools_per_item":
        return render_runtime_prompt("stream.plan_hint_multiple_tools")
    if reason == "unannounced_planned_tool":
        return render_runtime_prompt("stream.plan_hint_unannounced")
    if reason == "missing_answer_phase":
        return render_runtime_prompt("stream.plan_hint_answer_phase")
    if reason in {"unknown_dependency", "self_dependency", "dependency_cycle"}:
        return render_runtime_prompt("stream.plan_hint_dependency")
    return None


def _required_tool_coverage_summary(
    items: list[Any] | None,
    coordinator: PlanCoordinator,
) -> dict[str, int]:
    """只统计服务端声明的工具名，避免把未校验模型内容写入日志。"""

    counts = {tool_name: 0 for tool_name in coordinator.required_initial_tool_counts}
    if not items:
        return counts
    for item in items:
        if not isinstance(item, dict):
            continue
        planned_tools = item.get("planned_tools")
        if not isinstance(planned_tools, list):
            continue
        declared_tools = {tool_name for tool_name in planned_tools if isinstance(tool_name, str)}
        for tool_name in counts:
            other_required_tools = set(counts) - {tool_name}
            if tool_name in declared_tools and not other_required_tools.intersection(declared_tools):
                counts[tool_name] += 1
    return counts


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
    return cleaned, requested_item_id if isinstance(requested_item_id, str) else ""


async def process_plan_control_calls(
    *,
    tool_calls: list[dict],
    coordinator: PlanCoordinator,
    emitter: Any,
    required_recovery_tool_name: str | None = None,
) -> PlanControlResult:
    """先应用控制调用，再决定同轮外部调用；回执只含安全状态码。"""

    control_calls = [call for call in tool_calls if call.get("name") == UPDATE_PLAN_TOOL_NAME]
    external_calls = [call for call in tool_calls if call.get("name") != UPDATE_PLAN_TOOL_NAME]
    responses: dict[str, str] = {}
    accepted_control = False
    repairable_rejection = False
    repair_reasons: set[str] = set()

    for call in control_calls:
        call_id = str(call.get("id", ""))
        payload = _parse_arguments(call.get("arguments"))
        result = coordinator.apply_model_update(
            payload,
            required_active_tool_name=required_recovery_tool_name,
        )
        accepted = result.accepted
        reason = result.reason
        if not accepted:
            items = payload.get("plan") if isinstance(payload, dict) else None
            if not isinstance(items, list) and isinstance(payload, dict):
                items = payload.get("items")
            logger.info(
                "计划控制更新被拒绝: run_id=%s reason=%s item_count=%s required_tool_coverage=%s",
                coordinator.run_id,
                reason,
                min(len(items), 7) if isinstance(items, list) else None,
                _required_tool_coverage_summary(
                    items if isinstance(items, list) else None,
                    coordinator,
                ),
            )
        responses[call_id] = _response(
            status="accepted" if accepted else "rejected",
            reason=reason,
            revision=coordinator.revision,
            hint=None if accepted else _control_rejection_hint(reason, coordinator),
            canonical_plan=(
                coordinator.canonical_plan_for_model() if not accepted and coordinator.has_valid_model_plan else None
            ),
        )
        accepted_control = accepted_control or accepted
        is_repairable_rejection = (
            not accepted
            and reason != "plan_mode_off"
            and not (reason == "control_update_limit_reached" and coordinator.has_valid_model_plan)
        )
        repairable_rejection = repairable_rejection or is_repairable_rejection
        if is_repairable_rejection:
            repair_reasons.add(reason)
        if accepted and result.snapshot is not None:
            await emitter.plan_snapshot(**result.snapshot)

    round_failed = repairable_rejection and not accepted_control
    if coordinator.mode == "on" and external_calls and not coordinator.has_valid_model_plan:
        round_failed = True
        repair_reasons.add("plan_required")
        for call in external_calls:
            responses[str(call.get("id", ""))] = _response(
                status="not_executed",
                reason="plan_required",
                revision=coordinator.revision,
            )
        external_calls = []

    prepared_external_calls: list[dict] = []
    requested_item_ids: list[str | None] = []
    for call in external_calls:
        prepared_call, requested_item_id = _extract_plan_item_binding(call)
        prepared_external_calls.append(prepared_call)
        requested_item_ids.append(requested_item_id)
    external_calls = prepared_external_calls

    plan_item_ids: dict[str, str] = {}
    mapped_item_ids = coordinator.plan_item_ids_for_tools(
        [str(call.get("name", "")) for call in external_calls],
        requested_item_ids=requested_item_ids,
    )
    executable_external_calls: list[dict] = []
    # 只阻止同一模型批次把不同调用复用到同一任务；每轮重新建立，
    # 不影响服务端保持 retryable/running 的同一任务跨轮有界重试。
    round_bound_item_ids: set[str] = set()
    for call, plan_item_id, requested_item_id in zip(
        external_calls,
        mapped_item_ids,
        requested_item_ids,
    ):
        server_recovery_item_id = None
        if plan_item_id is None or requested_item_id is None:
            server_recovery_item_id = coordinator.sole_server_recovery_item_id_for_tool(str(call.get("name", "")))
        if server_recovery_item_id is not None:
            plan_item_id = server_recovery_item_id
        missing_required_binding = (
            coordinator.mode == "on"
            and coordinator.has_valid_model_plan
            and requested_item_id is None
            and server_recovery_item_id is None
        )
        if plan_item_id is not None and not missing_required_binding:
            if plan_item_id in round_bound_item_ids:
                round_failed = True
                responses[str(call.get("id", ""))] = _response(
                    status="not_executed",
                    reason="plan_item_already_bound",
                    revision=coordinator.revision,
                    hint=render_runtime_prompt("stream.plan_item_already_bound"),
                )
                continue
            round_bound_item_ids.add(plan_item_id)
            plan_item_ids[str(call.get("id", ""))] = plan_item_id
            executable_external_calls.append(call)
            continue
        invalid_explicit_binding = requested_item_id is not None and coordinator.has_valid_model_plan
        if (
            missing_required_binding
            or invalid_explicit_binding
            or (coordinator.mode == "on" and coordinator.has_valid_model_plan)
        ):
            round_failed = True
            repair_reasons.add("plan_item_required")
            responses[str(call.get("id", ""))] = _response(
                status="not_executed",
                reason="plan_item_required",
                revision=coordinator.revision,
                hint=render_runtime_prompt("stream.plan_item_required"),
            )
            continue
        executable_external_calls.append(call)
    external_calls = executable_external_calls

    tolerate_status_drift = bool(repair_reasons) and repair_reasons.issubset(_STATUS_DRIFT_REPAIR_REASONS)
    repair_result = (
        coordinator.record_repair_round_with_fallback(
            tolerate_status_drift=tolerate_status_drift,
        )
        if round_failed
        else None
    )
    if repair_result is not None:
        logger.info(
            "计划修复轮次已记录: run_id=%s attempt=%s limit=%s threshold_reached=%s "
            "fallback_adopted=%s has_valid_plan=%s",
            coordinator.run_id,
            repair_result.attempt_count,
            repair_result.attempt_limit,
            repair_result.attempt_count >= repair_result.attempt_limit,
            repair_result.fallback is not None,
            coordinator.has_valid_model_plan,
        )
    if repair_result is not None and repair_result.fallback is not None:
        fallback = repair_result.fallback
        if fallback.snapshot is not None:
            await emitter.plan_snapshot(**fallback.snapshot)

    return PlanControlResult(
        external_tool_calls=[
            {
                **call,
                **(
                    {"plan_item_id": plan_item_ids[str(call.get("id", ""))]}
                    if str(call.get("id", "")) in plan_item_ids
                    else {}
                ),
            }
            for call in external_calls
        ],
        tool_responses=responses,
        plan_item_ids=plan_item_ids,
        repair_exhausted=repair_result.exhausted if repair_result is not None else False,
        repair_attempt_count=repair_result.attempt_count if repair_result is not None else 0,
        repair_attempt_limit=repair_result.attempt_limit if repair_result is not None else 0,
    )
