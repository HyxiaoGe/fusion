"""在首个 LLM Round 前解析并冻结 Run 级能力包。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from app.ai.skills.registry import (
    LoadedSkillSnapshot,
    RunSkillResolution,
    load_skills_for_package,
)
from app.services.agent.plan_coordinator import PlanMode
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.dynamic_tool_discovery import (
    infer_network_kind,
    network_kind_is_denied,
)
from app.utils.run_capability_contract import (
    CAPABILITY_AUTO_PLAN_PACKAGES,
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_CONTROL_TOOL_NAMES,
    CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES,
    CAPABILITY_REASON_CODES,
    CAPABILITY_RECOVERY_PACKAGES,
    CAPABILITY_RECOVERY_TOOL_NAMES,
    validate_capability_resolution_semantics,
)

Confidence = Literal["high", "medium", "low"]
NetworkPolicy = Literal["allow", "no_web_search", "no_url_read", "no_network"]


ResolutionMode = Literal["routed", "degraded", "clarification"]


SCHEMA_VERSION = 2


ROUTER_VERSION = "2026-09-26.1"


_CANONICAL_EXTERNAL_TOOL_ORDER = CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER


_CONTROL_TOOL_NAMES = CAPABILITY_CONTROL_TOOL_NAMES


_PACKAGE_TOOLS = CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES


_AUTO_PLAN_PACKAGES = CAPABILITY_AUTO_PLAN_PACKAGES


_REASON_CODES = CAPABILITY_REASON_CODES


@dataclass(frozen=True)
class RunCapabilityResolution:
    schema_version: int
    router_version: str
    package_id: str
    confidence: Confidence
    resolution_mode: ResolutionMode
    reason_codes: tuple[str, ...]
    external_tool_names: tuple[str, ...]
    effective_plan_mode: PlanMode
    include_current_date: bool
    network_boundary_required: bool
    denied_product_tool_names: frozenset[str] = field(default_factory=frozenset)
    required_primary_tool_name: str | None = None
    skill_resolution: RunSkillResolution | None = None
    loaded_skills: tuple[LoadedSkillSnapshot, ...] = field(default=(), repr=False, compare=False)
    requires_catalog_evidence: bool = False


class CapabilityClassifier(Protocol):
    """把"用户消息 → 能力包"这一步从路由骨架里解耦出来。

    骨架（resolution 冻结、契约校验、工具/handler/binding/Prompt 的原子派生、
    Trajectory 投影）与分类实现无关。默认实现是本模块的规则分类器；换成模型分类
    只需替换这个可调用对象，`_CandidateRoute` 之后的流程完全不变（issue #24）。
    """

    def __call__(
        self,
        *,
        message: str,
        task_context_messages: list[object] | None,
        available_tool_names: list[str],
    ) -> "_CandidateRoute": ...


@dataclass(frozen=True)
class _CandidateRoute:
    package_id: str
    confidence: Confidence
    reason_codes: tuple[str, ...]
    include_current_date: bool
    resolution_mode: ResolutionMode = "routed"
    explicit_tool_names: tuple[str, ...] | None = None
    network_policy: NetworkPolicy = "allow"
    denied_tool_names: tuple[str, ...] = ()
    required_primary_tool_name: str | None = None


def resolve_run_capability_route(
    *,
    original_message: str | None,
    task_context_messages: list[object] | None,
    available_tool_names: list[str],
    requested_plan_mode: PlanMode,
    task_policy: AgentTaskPolicy,
    capabilities: dict,
    tools_disabled: bool,
    knowledge_grounded: bool,
    unavailable_tool_names: list[str] | None = None,
    load_skills_fn: Callable[..., Any] | None = None,
    classify_fn: CapabilityClassifier | None = None,
) -> RunCapabilityResolution:
    """根据受信运行态与当前用户消息解析最小能力包。"""

    message = _normalize_message(original_message)
    skill_loader = load_skills_fn or load_skills_for_package
    classify = classify_fn or classify_capability_request
    function_calling = capabilities.get("functionCalling") is True
    search_capable = capabilities.get("searchCapable") is True

    if knowledge_grounded:
        blocked_candidate = (
            _CandidateRoute(
                package_id="deep_research",
                confidence="high",
                reason_codes=("deep_research_mode",),
                include_current_date=True,
            )
            if task_policy.task_mode == "deep_research"
            else classify(
                message=message,
                task_context_messages=task_context_messages,
                available_tool_names=available_tool_names,
            )
        )
        blocked_tool_names = blocked_candidate.explicit_tool_names or _PACKAGE_TOOLS.get(
            blocked_candidate.package_id,
            (),
        )
        denied_product_tool_names = _resolve_denied_tool_names(blocked_candidate, available_tool_names)
        return _validated_resolution(
            _resolution(
                candidate=_CandidateRoute(
                    package_id="knowledge_grounded",
                    confidence="high",
                    reason_codes=("knowledge_grounded_mode",),
                    include_current_date=blocked_candidate.include_current_date,
                ),
                available_tool_names=available_tool_names,
                requested_plan_mode="off",
                function_calling=function_calling,
                tools_disabled=True,
                network_boundary_required=bool(blocked_tool_names),
                denied_product_tool_names=denied_product_tool_names,
            ),
            load_skills_fn=skill_loader,
        )

    if task_policy.task_mode == "deep_research":
        candidate = _CandidateRoute(
            package_id="deep_research",
            confidence="high",
            reason_codes=("deep_research_mode",),
            include_current_date=True,
        )
    else:
        candidate = classify(
            message=message,
            task_context_messages=task_context_messages,
            available_tool_names=available_tool_names,
        )

    denied_product_tool_names = _resolve_denied_tool_names(candidate, available_tool_names)

    package_requested_tools = candidate.explicit_tool_names or _PACKAGE_TOOLS.get(
        candidate.package_id,
        (),
    )
    requested_tools = tuple(name for name in package_requested_tools if name not in denied_product_tool_names)
    all_primary_denied = not requested_tools and bool(package_requested_tools)
    unavailable_tools = frozenset(unavailable_tool_names or ())
    available_tools = frozenset(available_tool_names) - unavailable_tools
    primary_name = candidate.required_primary_tool_name
    requires_chosen_primary = candidate.package_id in {"mobility_intercity", "mixed_itinerary"}
    missing_required_package_tool = candidate.package_id in {"verified_web", "travel_air_rail"} and any(
        name not in requested_tools or name not in available_tools for name in package_requested_tools
    )
    invalid_chosen_primary = (
        requires_chosen_primary
        and (
            primary_name not in package_requested_tools
            or primary_name not in requested_tools
            or primary_name not in available_tools
        )
    ) or (not requires_chosen_primary and primary_name is not None)
    requires_search = any(name in {"web_search", "url_read"} for name in requested_tools)
    needs_external_capability = bool(requested_tools)
    degraded_reason: str | None = None
    if all_primary_denied or missing_required_package_tool or invalid_chosen_primary:
        degraded_reason = "required_tools_unavailable"
    elif needs_external_capability and tools_disabled:
        degraded_reason = "tools_disabled"
    elif needs_external_capability and not function_calling:
        degraded_reason = "function_calling_unavailable"
    elif any(name in unavailable_tools for name in requested_tools):
        degraded_reason = "required_tools_unavailable"
    elif requires_search and not search_capable:
        degraded_reason = "search_capability_unavailable"

    if degraded_reason is not None:
        return _validated_resolution(
            _resolution(
                candidate=_CandidateRoute(
                    package_id="tools_unavailable",
                    confidence=candidate.confidence,
                    reason_codes=(degraded_reason,),
                    include_current_date=candidate.include_current_date,
                    resolution_mode="degraded",
                ),
                available_tool_names=available_tool_names,
                requested_plan_mode="off",
                function_calling=function_calling,
                tools_disabled=True,
                network_boundary_required=True,
                denied_product_tool_names=denied_product_tool_names,
            ),
            load_skills_fn=skill_loader,
        )

    resolution = _resolution(
        candidate=candidate,
        available_tool_names=[name for name in available_tool_names if name not in unavailable_tools],
        allow_recovery_tools=search_capable,
        requested_plan_mode=requested_plan_mode,
        function_calling=function_calling,
        tools_disabled=tools_disabled,
        denied_product_tool_names=denied_product_tool_names,
    )
    if candidate.package_id == "deep_research" and not frozenset(requested_tools).issubset(
        resolution.external_tool_names
    ):
        return _validated_resolution(
            _resolution(
                candidate=_CandidateRoute(
                    package_id="tools_unavailable",
                    confidence=candidate.confidence,
                    reason_codes=("required_tools_unavailable",),
                    include_current_date=candidate.include_current_date,
                    resolution_mode="degraded",
                ),
                available_tool_names=available_tool_names,
                requested_plan_mode="off",
                function_calling=function_calling,
                tools_disabled=True,
                network_boundary_required=True,
                denied_product_tool_names=denied_product_tool_names,
            ),
            load_skills_fn=skill_loader,
        )
    if needs_external_capability and not resolution.external_tool_names:
        return _validated_resolution(
            _resolution(
                candidate=_CandidateRoute(
                    package_id="tools_unavailable",
                    confidence=candidate.confidence,
                    reason_codes=("required_tools_unavailable",),
                    include_current_date=candidate.include_current_date,
                    resolution_mode="degraded",
                ),
                available_tool_names=available_tool_names,
                requested_plan_mode="off",
                function_calling=function_calling,
                tools_disabled=True,
                network_boundary_required=True,
                denied_product_tool_names=denied_product_tool_names,
            ),
            load_skills_fn=skill_loader,
        )
    return _validated_resolution(resolution, load_skills_fn=skill_loader)


def serialize_capability_resolution(resolution: RunCapabilityResolution) -> dict:
    """转换为可持久化的安全协议，不包含原文或自由文本。"""

    skill_resolution = resolution.skill_resolution
    if skill_resolution is None:
        raise ValueError("Run 能力包尚未冻结 Skill 终态")
    return {
        "schema_version": resolution.schema_version,
        "router_version": resolution.router_version,
        "package_id": resolution.package_id,
        "confidence": resolution.confidence,
        "resolution_mode": resolution.resolution_mode,
        "reason_codes": list(resolution.reason_codes),
        "external_tool_names": list(resolution.external_tool_names),
        "effective_plan_mode": resolution.effective_plan_mode,
        "include_current_date": resolution.include_current_date,
        "network_boundary_required": resolution.network_boundary_required,
        "denied_product_tool_names": sorted(resolution.denied_product_tool_names),
        "required_primary_tool_name": resolution.required_primary_tool_name,
        "skill_resolution": {
            "status": skill_resolution.status,
            "activation_source": skill_resolution.activation_source,
            "requested_skill_ids": list(skill_resolution.requested_skill_ids),
            "skills": [
                {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "content_sha256": skill.content_sha256,
                    "allowed_tool_names": list(skill.allowed_tool_names),
                    "section_id": skill.section_id,
                    "char_count": skill.char_count,
                }
                for skill in skill_resolution.skills
            ],
            "duration_ms": skill_resolution.duration_ms,
            "error_code": skill_resolution.error_code,
        },
    }


def classify_capability_request(
    *,
    message: str,
    task_context_messages: list[object] | None,
    available_tool_names: list[str],
) -> _CandidateRoute:
    """规则式选包已删除（#132）。

    字面层曾在模型之前按正则预选能力包，判错不可恢复；实测显示模型层自身即可
    承担该判断，而字面判据的主要作用是掩盖模型侧的既有缺陷。本函数只保留模型
    不可用时的 fail-closed 落点，不再做任何语义预选。
    """

    return _CandidateRoute(
        "clarification_only",
        "low",
        ("insufficient_capability_signal",),
        False,
        resolution_mode="clarification",
    )


def _resolution(
    *,
    candidate: _CandidateRoute,
    available_tool_names: list[str],
    requested_plan_mode: PlanMode,
    function_calling: bool,
    tools_disabled: bool,
    network_boundary_required: bool = False,
    allow_recovery_tools: bool = False,
    denied_product_tool_names: frozenset[str] = frozenset(),
) -> RunCapabilityResolution:
    requested_tools = candidate.explicit_tool_names or _PACKAGE_TOOLS.get(candidate.package_id, ())
    if allow_recovery_tools and candidate.package_id in CAPABILITY_RECOVERY_PACKAGES:
        requested_tools = (*requested_tools, *CAPABILITY_RECOVERY_TOOL_NAMES)
    available = frozenset(name for name in available_tool_names if isinstance(name, str) and name)
    tools = tuple(
        name
        for name in _canonicalize_tool_names(requested_tools)
        if name in available and name not in _CONTROL_TOOL_NAMES and name not in denied_product_tool_names
    )
    reason_codes = tuple(code for code in candidate.reason_codes if code in _REASON_CODES)
    if reason_codes != candidate.reason_codes:
        raise ValueError("能力路由包含未注册的 reason code")
    return RunCapabilityResolution(
        schema_version=SCHEMA_VERSION,
        router_version=ROUTER_VERSION,
        package_id=candidate.package_id,
        confidence=candidate.confidence,
        resolution_mode=candidate.resolution_mode,
        reason_codes=reason_codes,
        external_tool_names=tools,
        effective_plan_mode=_effective_plan_mode(
            package_id=candidate.package_id,
            requested_plan_mode=requested_plan_mode,
            function_calling=function_calling,
            tools_disabled=tools_disabled,
        ),
        include_current_date=candidate.include_current_date,
        network_boundary_required=network_boundary_required,
        denied_product_tool_names=denied_product_tool_names,
        required_primary_tool_name=candidate.required_primary_tool_name,
    )


def _resolve_denied_tool_names(candidate: _CandidateRoute, available_tool_names: list[str]) -> frozenset[str]:
    """把模型给出的禁止意图收敛到本次可用工具，并覆盖联网替代工具。"""

    available = frozenset(name for name in available_tool_names if isinstance(name, str) and name)
    denied = set(candidate.denied_tool_names)
    if candidate.network_policy == "allow":
        return frozenset(denied)
    for name in available:
        kind = infer_network_kind(name)
        if network_kind_is_denied(kind, candidate.network_policy):
            denied.add(name)
    return frozenset(denied)


def _validated_resolution(
    resolution: RunCapabilityResolution,
    *,
    load_skills_fn: Callable[..., Any],
) -> RunCapabilityResolution:
    skill_result = load_skills_fn(
        resolution.package_id,
        resolution.external_tool_names,
    )
    skill_resolution = skill_result.resolution
    if skill_resolution.status == "load_failed":
        resolution = RunCapabilityResolution(
            schema_version=SCHEMA_VERSION,
            router_version=ROUTER_VERSION,
            package_id="tools_unavailable",
            confidence=resolution.confidence,
            resolution_mode="degraded",
            reason_codes=("required_skill_unavailable",),
            external_tool_names=(),
            effective_plan_mode="off",
            include_current_date=resolution.include_current_date,
            network_boundary_required=True,
            denied_product_tool_names=resolution.denied_product_tool_names,
            skill_resolution=skill_resolution,
            loaded_skills=(),
        )
    else:
        resolution = replace(
            resolution,
            skill_resolution=skill_resolution,
            loaded_skills=tuple(skill_result.loaded_skills),
        )
    validate_capability_resolution_semantics(
        package_id=resolution.package_id,
        confidence=resolution.confidence,
        resolution_mode=resolution.resolution_mode,
        reason_codes=resolution.reason_codes,
        external_tool_names=resolution.external_tool_names,
        effective_plan_mode=resolution.effective_plan_mode,
        include_current_date=resolution.include_current_date,
        network_boundary_required=resolution.network_boundary_required,
        skill_resolution=resolution.skill_resolution,
    )
    if resolution.package_id in {"mobility_intercity", "mixed_itinerary"}:
        if resolution.required_primary_tool_name not in resolution.external_tool_names:
            raise ValueError("跨产品能力包缺少可执行的主工具")
    elif resolution.required_primary_tool_name is not None:
        raise ValueError("非跨产品能力包不得携带主工具门禁")
    return resolution


def _effective_plan_mode(
    *,
    package_id: str,
    requested_plan_mode: PlanMode,
    function_calling: bool,
    tools_disabled: bool,
) -> PlanMode:
    if not function_calling or tools_disabled:
        return "off"
    if package_id == "deep_research":
        return "on"
    if requested_plan_mode in {"on", "off"}:
        return requested_plan_mode
    return "auto" if package_id in _AUTO_PLAN_PACKAGES else "off"


def _canonicalize_tool_names(tool_names: tuple[str, ...]) -> tuple[str, ...]:
    known_order = {name: index for index, name in enumerate(_CANONICAL_EXTERNAL_TOOL_ORDER)}
    return tuple(sorted(set(tool_names), key=lambda name: (known_order.get(name, 10_000), name)))


def _normalize_message(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()
