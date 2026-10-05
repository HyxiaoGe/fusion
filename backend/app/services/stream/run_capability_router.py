"""在首个 LLM Round 前确定 Run 的工具边界。

不再调用分类模型，也不按预判收走工具：普通 Run 公告本次可用的全部工具，由回答模型
自行选择。这里只处理真实边界——用户选择的模式（深度研究、知识库）与运行环境
（工具被关闭、模型不支持工具调用）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.agent.plan_coordinator import PlanMode
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.utils.run_capability_contract import (
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_CONTROL_TOOL_NAMES,
    DEEP_RESEARCH_TOOL_NAMES,
)

SCHEMA_VERSION = 3


ROUTER_VERSION = "2026-10-05.1"


@dataclass(frozen=True)
class RunCapabilityResolution:
    schema_version: int
    router_version: str
    package_id: str
    reason_codes: tuple[str, ...]
    external_tool_names: tuple[str, ...]
    effective_plan_mode: PlanMode
    network_boundary_required: bool
    # 规模超过阈值时不直接公告、改由 tool_search 按需加载的 MCP 工具。
    deferred_tool_names: tuple[str, ...] = ()


def resolve_run_capability_route(
    *,
    available_tool_names: list[str],
    requested_plan_mode: PlanMode,
    task_policy: AgentTaskPolicy,
    capabilities: dict,
    tools_disabled: bool,
    knowledge_grounded: bool,
    deferred_tool_names: list[str] | None = None,
) -> RunCapabilityResolution:
    """按用户选择的模式与运行环境确定本次公告的工具。"""

    available = _canonicalize_tool_names(tuple(name for name in available_tool_names if isinstance(name, str) and name))
    deferred = tuple(name for name in deferred_tool_names or () if name not in available)
    function_calling = capabilities.get("functionCalling") is True

    if knowledge_grounded:
        return _resolution("knowledge_grounded", ("knowledge_grounded_mode",), plan_mode="off")
    if tools_disabled:
        return _unavailable("tools_disabled")
    if not function_calling:
        return _unavailable("function_calling_unavailable")

    if task_policy.task_mode == "deep_research":
        if capabilities.get("searchCapable") is not True:
            return _unavailable("search_capability_unavailable")
        if not set(DEEP_RESEARCH_TOOL_NAMES).issubset(available):
            return _unavailable("required_tools_unavailable")
        return _resolution(
            "deep_research",
            ("deep_research_mode",),
            tools=DEEP_RESEARCH_TOOL_NAMES,
            plan_mode="on",
        )

    if not available and not deferred:
        return _unavailable("required_tools_unavailable")
    return _resolution(
        "agent",
        ("all_available_tools",),
        tools=available,
        plan_mode="on" if requested_plan_mode == "on" else "off",
        deferred=deferred,
    )


def serialize_capability_resolution(resolution: RunCapabilityResolution) -> dict:
    """转换为可持久化的安全协议，不包含原文或自由文本。"""

    return {
        "schema_version": resolution.schema_version,
        "router_version": resolution.router_version,
        "package_id": resolution.package_id,
        "reason_codes": list(resolution.reason_codes),
        "external_tool_names": list(resolution.external_tool_names),
        "deferred_tool_names": list(resolution.deferred_tool_names),
        "effective_plan_mode": resolution.effective_plan_mode,
        "network_boundary_required": resolution.network_boundary_required,
    }


def _unavailable(reason_code: str) -> RunCapabilityResolution:
    return _resolution("tools_unavailable", (reason_code,), plan_mode="off")


def _resolution(
    package_id: str,
    reason_codes: tuple[str, ...],
    *,
    plan_mode: PlanMode,
    tools: tuple[str, ...] = (),
    deferred: tuple[str, ...] = (),
) -> RunCapabilityResolution:
    tools = tuple(name for name in tools if name not in CAPABILITY_CONTROL_TOOL_NAMES)
    return RunCapabilityResolution(
        schema_version=SCHEMA_VERSION,
        router_version=ROUTER_VERSION,
        package_id=package_id,
        reason_codes=reason_codes,
        external_tool_names=tools,
        effective_plan_mode=plan_mode,
        # 没有任何联网工具时告诉模型不能声称联网查证。
        network_boundary_required=not tools and not deferred,
        deferred_tool_names=deferred,
    )


def _canonicalize_tool_names(tool_names: tuple[str, ...]) -> tuple[str, ...]:
    known_order = {name: index for index, name in enumerate(CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER)}
    return tuple(sorted(set(tool_names), key=lambda name: (known_order.get(name, 10_000), name)))
