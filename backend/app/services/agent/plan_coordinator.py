"""Agent 计划的展示状态。

计划只用于让用户看到模型打算怎么做、做到了哪一步：服务端记录模型提交的计划，
随工具执行更新步骤状态，不校验计划结构、不要求工具绑定步骤、不据此拒绝或改写
模型的行动（2026-10-05 计划校验层已拆除）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PlanMode = Literal["auto", "on", "off"]
PlanStatus = Literal["pending", "running", "completed", "failed", "skipped", "blocked"]
PlanKind = Literal["reasoning", "search", "read", "synthesis", "answer", "other"]

_PLAN_STATUSES = frozenset({"pending", "running", "completed", "failed", "skipped", "blocked"})
_PLAN_KINDS = frozenset({"reasoning", "search", "read", "synthesis", "answer", "other"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "skipped", "blocked"})
_STATUS_ALIASES = {"in_progress": "running", "done": "completed", "todo": "pending"}
_MAX_PLAN_ITEMS = 14
_MAX_TITLE_LENGTH = 120
_MAX_ID_LENGTH = 64


@dataclass(frozen=True)
class PlanUpdateResult:
    accepted: bool
    reason: str
    snapshot: dict[str, Any] | None = None


@dataclass
class PlanCoordinator:
    """记录本次运行最新的模型计划，供轨迹与前端展示。"""

    run_id: str
    mode: PlanMode = "off"
    revision: int = 0
    reason: str = "not_started"
    items: list[dict[str, Any]] = field(default_factory=list)
    terminal_outcome: str | None = None

    @property
    def has_valid_model_plan(self) -> bool:
        return self.revision > 0 and bool(self.items)

    def apply_model_update(self, payload: Any) -> PlanUpdateResult:
        if self.mode == "off":
            return PlanUpdateResult(False, "plan_mode_off")
        if self.terminal_outcome is not None:
            return PlanUpdateResult(False, "run_already_terminal")
        items = _normalize_plan_items(payload, previous_items=self.items)
        if not items:
            return PlanUpdateResult(False, "invalid_plan_structure")
        self.items = _assign_user_visible_phases(items, previous_items=self.items)
        self.revision += 1
        self.reason = "model_update"
        return PlanUpdateResult(True, "model_update", self.snapshot())

    def snapshot(self, *, reason: str | None = None) -> dict[str, Any]:
        return {
            "plan_id": f"plan-{self.run_id}-model",
            "mode": self.mode,
            "source": "model",
            "revision": self.revision,
            "reason": reason or self.reason,
            "items": [dict(item) for item in self.items],
        }

    def plan_item_ids_for_tools(
        self,
        tool_names: list[str],
        *,
        requested_item_ids: list[str | None] | None = None,
    ) -> list[str | None]:
        """尽力把工具调用对应到计划步骤，只用于展示进度；对应不上就不对应。"""

        requested = requested_item_ids or [None] * len(tool_names)
        bound: set[str] = set()
        result: list[str | None] = []
        for tool_name, requested_item_id in zip(tool_names, requested):
            candidates = [
                str(item.get("id"))
                for item in self.items
                if tool_name in (item.get("planned_tools") or [])
                and item.get("status") not in _TERMINAL_STATUSES
                and str(item.get("id")) not in bound
            ]
            item_id = requested_item_id if requested_item_id in candidates else next(iter(candidates), None)
            if item_id is not None:
                bound.add(item_id)
            result.append(item_id)
        return result

    def mark_tools_started(self, plan_item_ids: list[str]) -> dict[str, Any] | None:
        return self._apply_statuses({item_id: "running" for item_id in plan_item_ids}, reason="tool_progress")

    def mark_tool_results(self, statuses: dict[str, PlanStatus]) -> dict[str, Any] | None:
        return self._apply_statuses(statuses, reason="tool_result")

    def pending_execution_items(self) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.items
            if item.get("planned_tools") and item.get("status") not in _TERMINAL_STATUSES
        ]

    def terminalize(self, outcome: str, *, has_final_answer: bool = False) -> dict[str, Any] | None:
        if not self.has_valid_model_plan or self.terminal_outcome is not None:
            return None
        self.terminal_outcome = outcome
        for item in self.items:
            status = item.get("status")
            if status in _TERMINAL_STATUSES:
                continue
            if outcome == "failed":
                item["status"] = "failed" if status == "running" else "skipped"
            elif outcome in {"interrupted", "superseded"}:
                item["status"] = "skipped"
            elif has_final_answer and not item.get("planned_tools"):
                # 不带工具的思考/回答步骤随最终回答完成。
                item["status"] = "completed"
            elif outcome == "stop" and has_final_answer:
                # 已经回答时，模型没走到的工具步骤如实标为跳过。
                item["status"] = "skipped"
            else:
                item["status"] = "blocked"
        self.revision += 1
        self.reason = f"terminal_{outcome}"
        return self.snapshot()

    def _apply_statuses(self, statuses: dict[str, PlanStatus], *, reason: str) -> dict[str, Any] | None:
        if not self.has_valid_model_plan or self.terminal_outcome is not None:
            return None
        changed = False
        for item in self.items:
            status = statuses.get(str(item.get("id")))
            if status is None or item.get("status") in _TERMINAL_STATUSES or item.get("status") == status:
                continue
            item["status"] = status
            changed = True
        if not changed:
            return None
        self.revision += 1
        self.reason = reason
        return self.snapshot()


def normalize_plan_mode(value: Any) -> PlanMode:
    return value if value in {"auto", "on", "off"} else "auto"


def _normalize_plan_items(payload: Any, *, previous_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容 items/plan、title/step、in_progress 等常见写法；无法识别的条目直接丢弃。"""

    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raw_items = payload.get("plan")
    if not isinstance(raw_items, list):
        return []

    previous_by_id = {str(item.get("id")): item for item in previous_items if item.get("id")}
    previous_id_by_title = {str(item.get("title")): str(item.get("id")) for item in previous_items if item.get("id")}
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items[:_MAX_PLAN_ITEMS]:
        if not isinstance(raw_item, dict):
            continue
        title = raw_item.get("title")
        if not isinstance(title, str) or not title.strip():
            title = raw_item.get("step")
        if not isinstance(title, str) or not title.strip():
            continue
        title = title.strip()[:_MAX_TITLE_LENGTH]
        item_id = raw_item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            item_id = previous_id_by_title.get(title) or f"step-{len(items) + 1}"
        item_id = item_id.strip()[:_MAX_ID_LENGTH]
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        previous = previous_by_id.get(item_id, {})

        status = _STATUS_ALIASES.get(raw_item.get("status"), raw_item.get("status"))
        if previous.get("status") in _TERMINAL_STATUSES or status not in _PLAN_STATUSES:
            # 工具执行得出的终态以服务端记录为准；模型没给或给了无法识别的状态时沿用旧值。
            status = previous.get("status", "pending")
        kind = raw_item.get("kind")
        if kind not in _PLAN_KINDS:
            kind = previous.get("kind", "other")
        depends_on = raw_item.get("depends_on")
        if not isinstance(depends_on, list):
            depends_on = [items[-1]["id"]] if items else []
        planned_tools = raw_item.get("planned_tools")
        if not isinstance(planned_tools, list):
            planned_tools = raw_item.get("tools")
        if not isinstance(planned_tools, list):
            planned_tools = list(previous.get("planned_tools") or [])
        items.append(
            {
                "id": item_id,
                "title": title,
                "status": status,
                "kind": kind,
                "depends_on": [str(value) for value in depends_on if isinstance(value, str) and value],
                "planned_tools": [str(value) for value in planned_tools if isinstance(value, str) and value],
            }
        )
    return items


def _assign_user_visible_phases(
    items: list[dict[str, Any]],
    *,
    previous_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把连续同类执行任务归入稳定阶段，并保证阶段 ID 在快照内唯一连续。"""

    previous_by_id = {str(item.get("id")): item for item in previous_items or [] if item.get("id")}
    current_item_ids = {str(item.get("id")) for item in items if item.get("id")}
    previous_phase_anchors: dict[str, str] = {}
    for item in previous_items or []:
        phase_id = item.get("phase_id")
        item_id = item.get("id")
        if phase_id and item_id and str(item_id) in current_item_ids:
            previous_phase_anchors.setdefault(str(phase_id), str(item_id))

    grouped_items: list[list[dict[str, Any]]] = []
    for item in items:
        phase_kind = _user_visible_phase_kind(item.get("kind"))
        previous_phase_id = previous_by_id.get(str(item.get("id")), {}).get("phase_id")
        current_previous_phase_ids = (
            {
                str(previous_by_id.get(str(grouped_item.get("id")), {}).get("phase_id"))
                for grouped_item in grouped_items[-1]
                if previous_by_id.get(str(grouped_item.get("id")), {}).get("phase_id")
            }
            if grouped_items
            else set()
        )
        keeps_existing_phase_boundary = (
            previous_phase_id is not None
            and bool(current_previous_phase_ids)
            and str(previous_phase_id) not in current_previous_phase_ids
        )
        if (
            grouped_items
            and _user_visible_phase_kind(grouped_items[-1][0].get("kind")) == phase_kind
            and not keeps_existing_phase_boundary
        ):
            grouped_items[-1].append(item)
        else:
            grouped_items.append([item])

    normalized: list[dict[str, Any]] = []
    reserved_previous_phase_ids = set(previous_phase_anchors)
    used_phase_ids: set[str] = set()
    for group in grouped_items:
        first = group[0]
        group_item_ids = {str(item.get("id")) for item in group}
        reusable_phase_id = next(
            (
                phase_id
                for phase_id, anchor_item_id in previous_phase_anchors.items()
                if anchor_item_id in group_item_ids and phase_id not in used_phase_ids
            ),
            None,
        )
        phase_id = reusable_phase_id or _new_user_visible_phase_id(
            str(first.get("id") or "task"),
            unavailable_phase_ids=reserved_previous_phase_ids | used_phase_ids,
        )
        used_phase_ids.add(phase_id)
        phase_title = _user_visible_phase_title(group)
        for item in group:
            normalized.append(
                {
                    **item,
                    "phase_id": phase_id,
                    "phase_title": phase_title,
                }
            )
    return normalized


def _new_user_visible_phase_id(
    first_item_id: str,
    *,
    unavailable_phase_ids: set[str],
) -> str:
    base_phase_id = f"phase-{first_item_id}"
    phase_id = base_phase_id
    suffix = 2
    while phase_id in unavailable_phase_ids:
        phase_id = f"{base_phase_id}-{suffix}"
        suffix += 1
    return phase_id


def _user_visible_phase_kind(kind: Any) -> str:
    return "synthesis" if kind in {"answer", "synthesis"} else str(kind or "other")


def _user_visible_phase_title(group: list[dict[str, Any]]) -> str:
    if len(group) == 1:
        return str(group[0].get("title") or "执行任务")[:80]
    phase_kind = _user_visible_phase_kind(group[0].get("kind"))
    return {
        "reasoning": "分析任务并制定方案",
        "search": "搜索并收集资料",
        "read": "读取并核验关键来源",
        "synthesis": "综合证据并输出结论",
        "other": "执行相关任务",
    }.get(phase_kind, "执行相关任务")
