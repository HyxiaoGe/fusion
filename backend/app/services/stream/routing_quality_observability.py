"""能力路由线上质量信号：从轨迹账本聚合“路由结果与实际工具使用是否一致”。

信号只是待人工复核的线索，不是判错：例如 fresh_web 未调用工具也可能是模型
合理地直接作答。所有工具口径都从 run_started 冻结的 capability_resolution
派生，不按包名硬编码工具清单。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from app.utils.run_capability_contract import CAPABILITY_RECOVERY_TOOL_NAMES
from app.utils.time import as_utc

ROUTING_QUALITY_SAMPLE_LIMIT = 50

# 只对已完成的 run 计算工具类信号；中断/失败/运行中的 run 工具调用不完整。
_TOOL_SIGNALS = ("no_tool_call", "web_only_fallback", "primary_tool_missed")
_ROUTE_SIGNALS = ("classifier_unavailable", "clarification_only", "tools_unavailable")
_SAMPLE_SIGNALS = frozenset({"classifier_unavailable", "clarification_only", *_TOOL_SIGNALS})
_TERMINAL_STATUS_BY_EVENT = {
    "run_completed": "completed",
    "run_interrupted": "interrupted",
    "run_failed": "failed",
}
_RECOVERY_TOOLS = frozenset(CAPABILITY_RECOVERY_TOOL_NAMES)


def _empty_signals() -> dict[str, int]:
    return {name: 0 for name in (*_ROUTE_SIGNALS, *_TOOL_SIGNALS)}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _run_signals(resolution: dict[str, Any], status: str, called: list[str]) -> list[str]:
    package_id = resolution.get("package_id")
    reason_codes = _string_list(resolution.get("reason_codes"))
    announced = set(_string_list(resolution.get("external_tool_names")))
    signals: list[str] = []
    if "classifier_unavailable" in reason_codes:
        signals.append("classifier_unavailable")
    if package_id == "clarification_only":
        signals.append("clarification_only")
    if package_id == "tools_unavailable":
        signals.append("tools_unavailable")
    if status != "completed" or not announced:
        return signals

    called_announced = {name for name in called if name in announced}
    product_tools = announced - _RECOVERY_TOOLS
    if not called_announced:
        signals.append("no_tool_call")
    elif product_tools and not called_announced & product_tools:
        signals.append("web_only_fallback")
    primary = resolution.get("required_primary_tool_name")
    if isinstance(primary, str) and primary and primary not in called_announced:
        signals.append("primary_tool_missed")
    return signals


def aggregate_routing_quality(
    rows: dict[str, Any],
    *,
    created_from: datetime,
    created_to: datetime,
) -> dict[str, Any]:
    """把有界的 run_started / 工具 / 终态事件聚合为按能力包的质量信号与可疑样本。"""

    called_by_run: dict[str, list[str]] = {}
    for event in rows.get("tool_events") or []:
        tool_name = (event.payload or {}).get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            called_by_run.setdefault(event.run_id, []).append(tool_name)
    status_by_run: dict[str, str] = {}
    for event in rows.get("terminal_events") or []:
        status = _TERMINAL_STATUS_BY_EVENT.get(event.event_type)
        if status is not None:
            status_by_run[event.run_id] = status

    summary: dict[str, Any] = {"total": 0, "completed": 0, "signals": _empty_signals()}
    packages: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    unrouted = 0
    samples: list[dict[str, Any]] = []
    for event in rows.get("starts") or []:
        resolution = (event.payload or {}).get("capability_resolution")
        package_id = resolution.get("package_id") if isinstance(resolution, dict) else None
        if not isinstance(package_id, str) or not package_id:
            unrouted += 1
            continue
        status = status_by_run.get(event.run_id, "running")
        status_counts[status] += 1
        called = called_by_run.get(event.run_id, [])
        signals = _run_signals(resolution, status, called)

        bucket = packages.setdefault(
            package_id,
            {"package_id": package_id, "total": 0, "completed": 0, "signals": _empty_signals()},
        )
        for target in (summary, bucket):
            target["total"] += 1
            if status == "completed":
                target["completed"] += 1
            for signal in signals:
                target["signals"][signal] += 1

        sample_signals = [signal for signal in signals if signal in _SAMPLE_SIGNALS]
        if sample_signals:
            samples.append(
                {
                    "run_id": event.run_id,
                    "conversation_id": event.conversation_id,
                    "started_at": as_utc(event.event_ts).isoformat(),
                    "package_id": package_id,
                    "status": status,
                    "signals": sample_signals,
                    "called_tools": sorted(set(called)),
                    "required_primary_tool_name": resolution.get("required_primary_tool_name"),
                }
            )

    samples.sort(key=lambda item: item["started_at"], reverse=True)
    return {
        "scope": {
            "created_from": created_from.isoformat(),
            "created_to": created_to.isoformat(),
            "unrouted_count": unrouted,
            "running_count": status_counts["running"],
            "interrupted_count": status_counts["interrupted"],
            "failed_count": status_counts["failed"],
            "sample_limit": ROUTING_QUALITY_SAMPLE_LIMIT,
            "sample_total": len(samples),
        },
        "summary": summary,
        "by_package": sorted(packages.values(), key=lambda item: (-item["total"], item["package_id"])),
        "samples": samples[:ROUTING_QUALITY_SAMPLE_LIMIT],
    }
