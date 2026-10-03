"""在首个 LLM Round 前解析并冻结 Run 级能力包。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.services.agent.plan_coordinator import PlanMode
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.dynamic_tool_discovery import (
    infer_network_kind,
    network_kind_is_denied,
)
from app.utils.run_capability_contract import (
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_CONTROL_TOOL_NAMES,
    CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES,
    CAPABILITY_PACKAGES,
    CAPABILITY_REASON_CODES,
    CAPABILITY_RECOVERY_PACKAGES,
    CAPABILITY_RECOVERY_TOOL_NAMES,
    McpRouteTool,
    validate_capability_resolution_semantics,
)

Confidence = Literal["high", "medium", "low"]
NetworkPolicy = Literal["allow", "no_web_search", "no_url_read", "no_network"]


ResolutionMode = Literal["routed", "degraded", "clarification"]
# 交付形态与能力包正交：document 时额外公告文档工具，正文写进文档而非聊天气泡。
OutputMode = Literal["chat", "document"]


SCHEMA_VERSION = 2


ROUTER_VERSION = "2026-09-26.1"


_CANONICAL_EXTERNAL_TOOL_ORDER = CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER


_CONTROL_TOOL_NAMES = CAPABILITY_CONTROL_TOOL_NAMES


_PACKAGE_TOOLS = CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES


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
    requires_catalog_evidence: bool = False
    output_mode: OutputMode = "chat"


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
        mcp_tool_catalog: tuple[McpRouteTool, ...] = (),
        existing_document_titles: tuple[str, ...] = (),
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
    output_mode: OutputMode = "chat"


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
    classify_fn: CapabilityClassifier | None = None,
    mcp_tool_catalog: tuple[McpRouteTool, ...] = (),
    existing_document_titles: tuple[str, ...] = (),
) -> RunCapabilityResolution:
    """根据受信运行态与当前用户消息解析最小能力包。"""

    message = _normalize_message(original_message)
    classify = classify_fn or classify_capability_request
    # 只在有可调用 MCP 工具时传目录，保持不认识该参数的分类器可用。
    catalog_kwargs: dict[str, Any] = {"mcp_tool_catalog": mcp_tool_catalog} if mcp_tool_catalog else {}
    if existing_document_titles:
        catalog_kwargs["existing_document_titles"] = existing_document_titles
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
                **catalog_kwargs,
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
            )
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
            **catalog_kwargs,
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
    candidate_spec = CAPABILITY_PACKAGES.get(candidate.package_id)
    requires_chosen_primary = candidate_spec is not None and candidate_spec.requires_primary_tool
    missing_required_package_tool = (
        candidate_spec is not None
        and candidate_spec.requires_all_tools
        and any(name not in requested_tools or name not in available_tools for name in package_requested_tools)
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
            )
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
            )
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
            )
        )
    return _validated_resolution(resolution)


def serialize_capability_resolution(resolution: RunCapabilityResolution) -> dict:
    """转换为可持久化的安全协议，不包含原文或自由文本。"""

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
        # chat 为默认形态，不写入以保持历史 Run 的协议与指纹不变。
        **({"output_mode": resolution.output_mode} if resolution.output_mode != "chat" else {}),
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
    不可用时的兜底落点，不再做任何语义预选。
    """

    return classifier_unavailable_route()


def classifier_unavailable_route() -> _CandidateRoute:
    """分类器失败（超时、调用出错、输出不合契约、配置缺失）时的统一兜底。

    失败是系统自己的问题，不代表用户没说清楚，所以不再退回 clarification_only 反问用户。
    改为开放网页搜索与读取、由回答模型按需调用；用户明确不许联网时，由回答模型的
    兜底提示词约束它不调用工具、直接基于已有知识作答（服务端此时无从得知该限制）。
    """

    return _CandidateRoute(
        package_id="fresh_web",
        confidence="low",
        reason_codes=("classifier_unavailable",),
        include_current_date=True,
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
        # 降级与澄清不进入文档模式：前者没有可用工具，后者需要先问清楚。
        output_mode=candidate.output_mode if candidate.resolution_mode == "routed" else "chat",
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


def _validated_resolution(resolution: RunCapabilityResolution) -> RunCapabilityResolution:
    validate_capability_resolution_semantics(
        package_id=resolution.package_id,
        confidence=resolution.confidence,
        resolution_mode=resolution.resolution_mode,
        reason_codes=resolution.reason_codes,
        external_tool_names=resolution.external_tool_names,
        effective_plan_mode=resolution.effective_plan_mode,
        include_current_date=resolution.include_current_date,
        network_boundary_required=resolution.network_boundary_required,
    )
    if CAPABILITY_PACKAGES[resolution.package_id].requires_primary_tool:
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
    spec = CAPABILITY_PACKAGES.get(package_id)
    # 未知能力包在随后的契约校验中拒绝，这里只需给出不越权的计划模式。
    plan_modes = spec.plan_modes if spec is not None else frozenset({"on", "off"})
    if "off" not in plan_modes:
        # 必须开启计划的包（deep_research）不受请求的计划模式影响。
        return "on"
    if requested_plan_mode in {"on", "off"}:
        return requested_plan_mode
    return "auto" if "auto" in plan_modes else "off"


def _canonicalize_tool_names(tool_names: tuple[str, ...]) -> tuple[str, ...]:
    known_order = {name: index for index, name in enumerate(_CANONICAL_EXTERNAL_TOOL_ORDER)}
    return tuple(sorted(set(tool_names), key=lambda name: (known_order.get(name, 10_000), name)))


def _normalize_message(value: str | None) -> str:
    """分类器是模型，原样保留大小写与换行；小写折叠是字面匹配时代的遗留，会改变模型判断。"""

    if not isinstance(value, str):
        return ""
    return value.strip()
