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
from app.services.stream.agent_plan_tool_policy import (
    ProductCapabilitySignals,
    resolve_product_capability_signals,
)
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.run_capability_request_signals import (
    _POSITIVE_URL_TOOL_NAME_RE,
    _POSITIVE_WEB_SEARCH_ACTION_RE,
    _POSITIVE_WEB_TOOL_NAME_RE,
    _URL_RE,
    _extract_directive_scope,
    _extract_request_signals,
    _overlaps_any,
    _RequestSignals,
)
from app.utils.location_names import (
    EN_KNOWN_LANDMARK_NAMES,
    are_distinct_known_cities,
    is_known_location_name,
)
from app.utils.run_capability_contract import (
    CAPABILITY_AUTO_PLAN_PACKAGES,
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_CONTROL_TOOL_NAMES,
    CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES,
    CAPABILITY_REASON_CODES,
    CAPABILITY_RECOVERY_PACKAGES,
    CAPABILITY_RECOVERY_TOOL_NAMES,
    is_authorized_mcp_tool_alias,
    validate_capability_resolution_semantics,
)

Confidence = Literal["high", "medium", "low"]
ResolutionMode = Literal["routed", "degraded", "clarification"]

SCHEMA_VERSION = 2
ROUTER_VERSION = "2026-09-20.1"

_CANONICAL_EXTERNAL_TOOL_ORDER = CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER
_CONTROL_TOOL_NAMES = CAPABILITY_CONTROL_TOOL_NAMES

_GREETING_RE = re.compile(
    r"^(?:(?:你?好|嗨)(?:[，,\s]*很高兴见到你)?|hi|hello|早上好|下午好|晚上好|很高兴见到你)"
    r"[呀啊！!。\s]*$",
    re.IGNORECASE,
)
_NOUN_DEFINITION_RE = re.compile(
    r"^what (?:is|are)\s+(?P<noun>(?:an?\s+)?(?:weather\s+forecasts?|current\s+price|"
    r"(?:primary|official|authoritative)\s+sources?)|breaking\s+news|"
    r"official\s+(?:documentation|announcements?))(?P<tail>.*?)[?.!]*$",
    re.IGNORECASE,
)
_SAFE_DEFINITION_TAIL_RE = re.compile(
    r"^(?:|\s+in\s+[a-z][a-z0-9 &'/-]{0,63}|"
    r",\s*in\s+(?:simple|plain|everyday)\s+terms|"
    r"\s+for\s+(?:beginners?|a\s+beginner)|"
    r",\s*(?:simply|briefly))$",
    re.IGNORECASE,
)
_SAFE_BASIC_DEFINITION_TAIL_RE = re.compile(
    r"^(?:|,?\s*in\s+(?:simple|plain|everyday)\s+terms|"
    r"\s+for\s+(?:beginners?|a\s+beginner)|"
    r",\s*(?:simply|briefly))$",
    re.IGNORECASE,
)
_SAFE_CURRENT_PRICE_DEFINITION_TAIL_RE = re.compile(
    r"^(?:|\s+in\s+(?:finance|economics|accounting|financial\s+markets?|market\s+analysis)|"
    r",?\s*in\s+(?:simple|plain|everyday)\s+terms|"
    r"\s+for\s+(?:beginners?|a\s+beginner)|"
    r",\s*(?:simply|briefly))$",
    re.IGNORECASE,
)
_SAFE_WEATHER_DEFINITION_TAIL_RE = re.compile(
    r"^(?:|\s+in\s+(?:meteorology|forecasting|weather\s+science|climate\s+science)|"
    r",?\s*in\s+(?:simple|plain|everyday)\s+terms|"
    r"\s+for\s+(?:beginners?|a\s+beginner)|"
    r",\s*(?:simply|briefly))$",
    re.IGNORECASE,
)
_EXTERNAL_QUERY_DEFINITION_TAIL_RE = re.compile(
    r"\b(?:about|regarding|latest|newest|today|tomorrow|yesterday|now|"
    r"announcement|release|price\s+of)\b",
    re.IGNORECASE,
)
_DEFINITIONAL_KNOWLEDGE_RE = re.compile(
    r"^(?:what (?:is|are)\s+the\s+difference\s+between\s+.+|"
    r"what does\s+.{1,96}\b(?:mean|usually\s+contain)\b|"
    r"how do\s+.{1,96}\bwork\b|"
    r"explain what\s+.{1,96}\bmeans?\b|"
    r"(?:什么是|为什么|为何).+|"
    r"解释(?:一下)?.*(?:区别|含义|意思|概念|原理))",
    re.IGNORECASE,
)
_EN_PRODUCT_SEQUENCE_ACTION = (
    r"(?:do not|don['’]t|dont|never|not|avoid|without|find|search|show|book|compare|"
    r"check|look for|get|recommend|give|provide|plan|route|directions?)"
)
_EN_PRODUCT_SEQUENCE_BOUNDARY = (
    r"(?:\s[—–]\s|[—–]{2}|\s+(?:and\s+then|then|afterwards|later|finally)\b"
    rf"(?=\s+{_EN_PRODUCT_SEQUENCE_ACTION}))"
)
_EN_PRODUCT_CLAUSE_TEXT_ATOM = rf"(?:(?!{_EN_PRODUCT_SEQUENCE_BOUNDARY})[^,;:.!?])"
_ZH_PRODUCT_SEQUENCE_ACTION = (
    r"(?:不要|不用|无需|不需要|不必|没必要|没有必要|用不着|别|请勿|禁止|严禁|不得|不可|"
    r"查|查询|搜索|找|预订|订|购买|买|比较|对比|推荐|给出|提供|查看|获取|规划)"
)
_ZH_PRODUCT_SEQUENCE_BOUNDARY = (
    r"(?:\s[—–]\s|[—–]{2}|"
    rf"(?:并且|并|然后|随后|最后|但(?:是)?|不过|而(?:是|要)?)(?={_ZH_PRODUCT_SEQUENCE_ACTION}))"
)
_ZH_PRODUCT_CLAUSE_TEXT_ATOM = rf"(?:(?!{_ZH_PRODUCT_SEQUENCE_BOUNDARY})[^，,。；;：:！？!?])"
_PRODUCT_DIRECTIVE_BOUNDARY_RE = re.compile(
    r"(?:[，,。；;：:！？!?]|\s+[—–]\s+|[—–]{2}|"
    r"\b(?:and\s+then|then|afterwards|later|finally)\b|"
    r"(?:并且|并|然后|随后|最后|但(?:是)?|不过|而(?:是|要)?))",
    re.IGNORECASE,
)
_EN_WEATHER_TASK_RE = re.compile(
    r"\b(?:weather|temperature|rain|snow|wind) forecast\b|"
    r"\b(?:check|show|find|get|look up)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,24}}\bweather\b|"
    r"\b(?:weather|temperature|rain(?:ing)?|snow(?:ing)?|wind)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,48}}"
    r"\b(?:in|for|at|today|tomorrow|this week|next week)\b|"
    r"\b(?:will it|is it going to) (?:rain|snow)\b|"
    r"\bis it (?:raining|snowing)\b",
    re.IGNORECASE,
)
_EN_FLIGHT_TASK_RE = re.compile(
    r"\b(?:find|search|show|book|compare|check|look for)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,48}}"
    r"\b(?:flights?|airfare|plane tickets?)\b|"
    r"\b(?:flights?|airfare|plane tickets?)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,64}}\bfrom\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{1,48}}\bto\b",
    re.IGNORECASE,
)
_EN_TRAIN_TASK_RE = re.compile(
    r"\b(?:find|search|show|book|compare|check|look for)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,48}}"
    r"\b(?:trains?|rail tickets?)\b|"
    r"\b(?:trains?|rail tickets?)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,64}}\bfrom\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{1,48}}\bto\b",
    re.IGNORECASE,
)
_ZH_FLIGHT_TASK_RE = re.compile(
    r"(?:查|查询|搜索|找|预订|订|购买|买|比较|对比|推荐)"
    rf"{_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,24}}(?:航班|机票|飞机)|"
    rf"(?:航班|机票|飞机){_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,24}}"
    r"(?:查|查询|搜索|找|预订|订|购买|买|时间|价格|多少钱)|"
    r"(?:坐|乘).{0,4}飞机",
)
_ZH_TRAIN_TASK_RE = re.compile(
    r"(?:查|查询|搜索|找|预订|订|购买|买|比较|对比|推荐)"
    rf"{_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,24}}(?:高铁|动车|火车|列车|车次)|"
    rf"(?:高铁|动车|火车|列车|车次){_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,24}}"
    r"(?:查|查询|搜索|找|预订|订|购买|买|时间|价格|多少钱)|"
    r"(?:坐|乘).{0,4}(?:高铁|动车|火车|列车)",
)
_ZH_AIR_RAIL_COMPARISON_RE = re.compile(
    r"(?:飞机|航班|机票).{0,20}(?:高铁|动车|火车|列车).{0,12}(?:还是|比较|对比|哪个好|更好)|"
    r"(?:高铁|动车|火车|列车).{0,20}(?:飞机|航班|机票).{0,12}(?:还是|比较|对比|哪个好|更好)|"
    r"(?:飞机|航班|机票).{0,8}(?:还是|比较|对比).{0,8}(?:高铁|动车|火车|列车)|"
    r"(?:高铁|动车|火车|列车).{0,8}(?:还是|比较|对比).{0,8}(?:飞机|航班|机票)",
)
_EN_PLACE_TASK_RE = re.compile(
    r"\b(?:find|search|show|recommend|look for)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,64}}"
    r"\b(?:nearby|near|coffee shops?|cafes?|restaurants?|hotels?|attractions?|"
    r"places? to (?:eat|visit|stay)|things to do)\b",
    re.IGNORECASE,
)
_EN_ROUTE_RELATION_RE = re.compile(
    r"\b(?:how (?:do|can|should) i get|i (?:need|want|plan) to (?:travel|go)|"
    r"directions?|route|public transit|driving|walking|cycling)\b"
    rf"{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,48}}\bfrom\s+(?P<origin>[a-z][a-z0-9 .'-]{{0,64}}?)\s+"
    rf"to\s+(?P<destination>[a-z]{_EN_PRODUCT_CLAUSE_TEXT_ATOM}{{0,63}}?)"
    rf"(?=\s+(?:by|via)\s+|[?.!,;:]|{_EN_PRODUCT_SEQUENCE_BOUNDARY}|$)",
    re.IGNORECASE,
)
_EN_STRONG_MOBILITY_ACTION_RE = re.compile(
    r"\b(?:how (?:do|can|should) i get|i (?:need|want|plan) to (?:travel|go)|"
    r"directions?|public transit|driving|walking|cycling)\b",
    re.IGNORECASE,
)
_EN_ROUTE_MODE_SEGMENT_RE = re.compile(
    r"\b(?:by|via)\s+(?P<modes>[^?.!]{1,96})(?=[?.!]|$)",
    re.IGNORECASE,
)
_EN_NATURAL_INTERCITY_RE = re.compile(
    r"\bi(?:'m| am)(?: currently)? in (?P<origin>[a-z][a-z .'-]{1,40}?)"
    r"(?:,\s*(?:and\s+)?|\s+and\s+)i (?:want|need|plan) to (?:go|travel) to "
    r"(?P<destination>[a-z][a-z .'-]{1,40}?)(?:[.?!]|$)",
    re.IGNORECASE,
)
_EN_PHYSICAL_LOCATION_SUFFIX_RE = re.compile(
    r"\b(?:station|airport|terminal|square|park|museum|hospital|hotel|mall|"
    r"road|street|bridge|tower)\b$",
    re.IGNORECASE,
)
_EN_ABSTRACT_ROUTE_CONTEXT_RE = re.compile(
    r"\b(?:process|workflow|career|promotion|business|technical|development|growth)\b",
    re.IGNORECASE,
)
_EN_ABSTRACT_ROUTE_ENDPOINT_RE = re.compile(
    r"\b(?:draft|publication|requirement|review|production|deployment|release|"
    r"engineer|architect|manager|director|career|role|rank|level|cost|profit|loss|"
    r"awareness|conversion|familiar|unfamiliar|beginner|expert|idea|implementation|"
    r"cold start|scale|scaling)\b",
    re.IGNORECASE,
)
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
    denied_product_tool_names = _resolve_denied_product_tool_names(_extract_request_signals(message))
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

    requested_tools = candidate.explicit_tool_names or _PACKAGE_TOOLS.get(
        candidate.package_id,
        (),
    )
    requested_tools = tuple(name for name in requested_tools if name not in denied_product_tool_names)
    if not requested_tools and (candidate.explicit_tool_names or _PACKAGE_TOOLS.get(candidate.package_id, ())):
        candidate = _CandidateRoute(
            package_id="clarification_only",
            confidence="low",
            reason_codes=("insufficient_capability_signal",),
            include_current_date=False,
            resolution_mode="clarification",
        )
    unavailable_tools = frozenset(unavailable_tool_names or ())
    requires_search = any(name in {"web_search", "url_read"} for name in requested_tools)
    needs_external_capability = bool(requested_tools)
    degraded_reason: str | None = None
    if needs_external_capability and tools_disabled:
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


@dataclass(frozen=True)
class _EnglishRouteSignals:
    """英文出行表达的解析结果；中文路径不依赖这些字段。"""

    relation: tuple[str, str] | None
    abstract_route: bool
    route: bool
    intercity: bool
    requested_modes: frozenset[str]
    mode_directive_present: bool
    mode_flight: bool
    mode_train: bool
    mode_route: bool
    local_route: bool
    intercity_route: bool


def _classify_literal_layer(
    request: _RequestSignals,
    available_tool_names: list[str] | None = None,
) -> _CandidateRoute | None:
    """只处理靠字面就能判定的能力包；判不出来返回 None 交给下一层。"""

    routing_message = request.routing_message
    web_search_denied = request.web_search_denied
    url_read_denied = request.url_read_denied
    explicit_web_search_request = request.explicit_web_search_request
    url_read_request = request.url_read_request
    verified_web_request = request.verified_web_request
    independent_verified_web_request = request.independent_verified_web_request
    fresh_web_request = request.fresh_web_request

    if _is_definitional_knowledge_request(routing_message):
        return _CandidateRoute(
            "direct",
            "high",
            ("stable_knowledge_question",),
            False,
        )

    if (
        url_read_request
        and not url_read_denied
        and not web_search_denied
        and (independent_verified_web_request or explicit_web_search_request or fresh_web_request)
    ):
        return _CandidateRoute(
            "verified_web",
            "high",
            ("verified_source_request",),
            True,
        )
    if not url_read_denied and url_read_request:
        return _CandidateRoute("url_read", "high", ("explicit_url_read",), False)
    if not web_search_denied and not url_read_denied and verified_web_request:
        return _CandidateRoute(
            "verified_web",
            "high",
            ("verified_source_request",),
            True,
        )
    denied_search_request = web_search_denied and bool(
        verified_web_request or fresh_web_request or explicit_web_search_request
    )
    denied_url_request = url_read_denied and url_read_request
    if denied_search_request or denied_url_request:
        return _CandidateRoute(
            "clarification_only",
            "low",
            ("insufficient_capability_signal",),
            False,
            resolution_mode="clarification",
        )
    explicit_alias = (
        None
        if request.all_network_denied or available_tool_names is None
        else _resolve_explicit_authorized_alias(routing_message, available_tool_names)
    )
    if explicit_alias is not None and _is_complete_explicit_alias_intent(routing_message, explicit_alias):
        return _CandidateRoute(
            "mcp_explicit",
            "high",
            ("explicit_authorized_tool_alias",),
            request.include_current_date,
            explicit_tool_names=(explicit_alias,),
        )
    if _GREETING_RE.search(routing_message):
        return _CandidateRoute("direct", "high", ("direct_greeting",), False)
    return None


def _is_complete_explicit_alias_intent(message: str, alias: str) -> bool:
    """只把带单一任务参数的精确 alias 指令留在字面层。"""

    directives = _explicit_alias_directives(message, alias)
    if not directives or not directives[-1][2]:
        return False
    suffix = message[directives[-1][1] :]
    return not _has_alias_followup_clause(suffix)


def _has_alias_followup_clause(suffix: str) -> bool:
    """识别 alias 参数之后明确开始的第二子句，不推断其中的产品语义。"""

    for boundary in re.finditer(
        r"(?P<connector>然后|再|并且|随后|\b(?:and\s+then|then|also)\b)|"
        r"(?P<natural_connector>\b(?:and)\b|并)|"
        r"(?P<sentence_boundary>[，,；;。！？!?])",
        suffix,
        re.IGNORECASE,
    ):
        task_parameter = suffix[: boundary.start()].strip(" \t，,；;。！？!?")
        followup = suffix[boundary.end() :].strip(" \t，,；;。！？!?")
        if boundary.group("natural_connector") is not None:
            if _starts_alias_followup_task(followup):
                return True
            continue
        if followup and (boundary.group("connector") is not None or task_parameter):
            return True
    return False


def _starts_alias_followup_task(followup: str) -> bool:
    """裸 and/并 后必须有独立任务启动词，避免误拆单一 alias 参数。"""

    return bool(
        re.match(
            r"(?:"
            r"(?:请|帮我|麻烦)?(?:查|查询|找|搜索|告诉|帮|规划|比较|预订|安排|导航)"
            r"|(?:please\s+)?(?:find|search|check|tell|show|plan|compare|book|navigate|look\s+up|get|recommend|give|provide)\b"
            r")",
            followup,
            re.IGNORECASE,
        )
    )


def _extract_english_route_signals(request: _RequestSignals) -> _EnglishRouteSignals:
    routing_message = request.routing_message

    english_relation = _extract_english_route_relation(routing_message)
    english_abstract_route = bool(
        english_relation
        and _is_english_abstract_route_relation(
            routing_message,
            english_relation[0],
            english_relation[1],
        )
    )
    english_known_pair = bool(
        english_relation and is_known_location_name(english_relation[0]) and is_known_location_name(english_relation[1])
    )
    english_strong_physical_pair = bool(
        english_relation
        and _EN_STRONG_MOBILITY_ACTION_RE.search(routing_message)
        and _is_english_physical_location(english_relation[0])
        and _is_english_physical_location(english_relation[1])
    )
    english_route = bool(
        english_relation and not english_abstract_route and (english_known_pair or english_strong_physical_pair)
    )
    english_intercity = bool(
        english_relation
        and not english_abstract_route
        and are_distinct_known_cities(english_relation[0], english_relation[1])
    )
    requested_english_route_modes = _extract_english_route_modes(routing_message) if english_relation else frozenset()
    english_route_mode_directive_present = bool(english_relation and _EN_ROUTE_MODE_SEGMENT_RE.search(routing_message))
    trusted_english_endpoints = english_route or english_intercity
    authorized_english_route_modes = requested_english_route_modes if trusted_english_endpoints else frozenset()
    english_route_mode_flight = "flight" in authorized_english_route_modes
    english_route_mode_train = "train" in authorized_english_route_modes
    english_route_mode_route = "route" in authorized_english_route_modes
    english_local_route = bool(
        english_route
        and not english_intercity
        and (not requested_english_route_modes or english_route_mode_route or english_route_mode_train)
    )
    english_intercity_route = bool(english_intercity and english_route_mode_route)
    return _EnglishRouteSignals(
        relation=english_relation,
        abstract_route=english_abstract_route,
        route=english_route,
        intercity=english_intercity,
        requested_modes=requested_english_route_modes,
        mode_directive_present=english_route_mode_directive_present,
        mode_flight=english_route_mode_flight,
        mode_train=english_route_mode_train,
        mode_route=english_route_mode_route,
        local_route=english_local_route,
        intercity_route=english_intercity_route,
    )


@dataclass(frozen=True)
class _ProductToolRequests:
    """经过否定、再授权与全局断网裁剪后，本次请求实际成立的产品工具。"""

    explicit_route: bool
    flight: bool
    train: bool
    weather: bool
    place: bool
    intercity_relation: bool
    denied_requests: frozenset[str]


def _resolve_denied_product_tool_names(request: _RequestSignals) -> frozenset[str]:
    """独立解析产品工具禁用，供规则与模型候选共同使用。"""

    return frozenset(
        tool_name
        for tool_name in (
            "weather_forecast",
            "local_place_search",
            "route_compare",
            "search_flights",
            "search_trains",
        )
        if _is_product_tool_finally_denied(request.control_message, tool_name)
    )


def _resolve_product_tool_requests(
    request: _RequestSignals,
    signals: ProductCapabilitySignals,
    english: _EnglishRouteSignals,
    denied_product_requests: frozenset[str],
) -> _ProductToolRequests:
    """仅按既有产品意图判据选出本次实际成立的工具。"""

    routing_message = request.routing_message
    control_message = request.control_message
    all_network_denied = request.all_network_denied
    english_intercity = english.intercity
    english_local_route = english.local_route
    english_intercity_route = english.intercity_route
    english_route_mode_flight = english.mode_flight
    english_route_mode_train = english.mode_train
    english_route_mode_route = english.mode_route
    english_route_mode_directive_present = english.mode_directive_present
    intercity_relation = (
        signals.endpoint_relation and signals.intercity_mobility and signals.intercity_endpoints
    ) or english_intercity
    reauthorized_route_clause = _final_reauthorized_natural_product_clause(control_message, "route_compare")
    reauthorized_route = bool(
        reauthorized_route_clause
        and resolve_product_capability_signals(
            original_message=reauthorized_route_clause,
            task_context_messages=None,
        ).explicit_route
    )

    explicit_route_requested = bool(
        not all_network_denied
        and (
            (
                signals.explicit_route
                and not (english_intercity and english_route_mode_directive_present and not english_route_mode_route)
            )
            or english_local_route
            or english_intercity_route
            or _has_final_positive_product_tool_directive(control_message, "route_compare")
            or reauthorized_route
        )
    )
    flight_requested = bool(
        not all_network_denied
        and (
            _ZH_FLIGHT_TASK_RE.search(routing_message)
            or _EN_FLIGHT_TASK_RE.search(routing_message)
            or _ZH_AIR_RAIL_COMPARISON_RE.search(routing_message)
            or (english_intercity and english_route_mode_flight)
            or _has_final_positive_product_tool_directive(control_message, "search_flights")
        )
    )
    train_requested = bool(
        not all_network_denied
        and (
            _ZH_TRAIN_TASK_RE.search(routing_message)
            or _EN_TRAIN_TASK_RE.search(routing_message)
            or _ZH_AIR_RAIL_COMPARISON_RE.search(routing_message)
            or (english_intercity and english_route_mode_train)
            or _has_final_positive_product_tool_directive(control_message, "search_trains")
        )
    )
    weather_requested = bool(
        not all_network_denied
        and (
            signals.weather
            or _EN_WEATHER_TASK_RE.search(routing_message)
            or _has_final_positive_product_tool_directive(control_message, "weather_forecast")
        )
    )
    place_requested = bool(
        not all_network_denied
        and (
            signals.place
            or _EN_PLACE_TASK_RE.search(routing_message)
            or _has_final_positive_product_tool_directive(control_message, "local_place_search")
        )
    )
    active_denials = {
        tool_name
        for requested, tool_name in (
            (weather_requested, "weather_forecast"),
            (place_requested, "local_place_search"),
            (explicit_route_requested, "route_compare"),
            (flight_requested, "search_flights"),
            (train_requested, "search_trains"),
        )
        if requested and tool_name in denied_product_requests
    }
    if "route_compare" in denied_product_requests:
        active_denials.add("route_compare")
    explicit_route = explicit_route_requested and "route_compare" not in active_denials
    flight = flight_requested and "search_flights" not in active_denials
    train = train_requested and "search_trains" not in active_denials
    weather = weather_requested and "weather_forecast" not in active_denials
    place = place_requested and "local_place_search" not in active_denials
    if all_network_denied:
        intercity_relation = False
    return _ProductToolRequests(
        explicit_route=explicit_route,
        flight=flight,
        train=train,
        weather=weather,
        place=place,
        intercity_relation=intercity_relation,
        denied_requests=frozenset(active_denials),
    )


def _classify_product_layer(
    request: _RequestSignals,
    signals: ProductCapabilitySignals,
    english: _EnglishRouteSignals,
) -> _CandidateRoute | None:
    """按已成立的产品工具集合选包；没有任何产品信号时返回 None。"""

    include_current_date = request.include_current_date
    english_route_mode_directive_present = english.mode_directive_present
    requests = _resolve_product_tool_requests(
        request,
        signals,
        english,
        _resolve_denied_product_tool_names(request),
    )
    explicit_route = requests.explicit_route
    flight = requests.flight
    train = requests.train
    weather = requests.weather
    place = requests.place
    intercity_relation = requests.intercity_relation
    denied_product_requests = requests.denied_requests

    if (
        intercity_relation
        and explicit_route
        and not english_route_mode_directive_present
        and not flight
        and not train
        and not weather
        and not place
    ):
        return _CandidateRoute(
            "mobility_intercity",
            "medium",
            ("origin_destination_relation", "intercity_locations"),
            True,
        )

    product_tools = tuple(
        name
        for enabled, name in (
            (weather, "weather_forecast"),
            (place, "local_place_search"),
            (explicit_route, "route_compare"),
            (flight, "search_flights"),
            (train, "search_trains"),
        )
        if enabled
    )
    if denied_product_requests and not product_tools:
        return _CandidateRoute(
            "clarification_only",
            "low",
            ("insufficient_capability_signal",),
            False,
            resolution_mode="clarification",
        )
    if len(product_tools) > 3:
        return _CandidateRoute(
            "clarification_only",
            "low",
            ("insufficient_capability_signal",),
            False,
            resolution_mode="clarification",
        )
    if len(product_tools) >= 2 and frozenset(product_tools) != frozenset({"search_flights", "search_trains"}):
        return _CandidateRoute(
            "mixed_itinerary",
            "high",
            ("mixed_itinerary_request",),
            True,
            explicit_tool_names=product_tools,
        )
    if flight and train:
        return _CandidateRoute(
            "travel_air_rail",
            "high",
            ("air_rail_comparison",),
            True,
        )
    if flight:
        return _CandidateRoute("flight", "high", ("explicit_flight_request",), True)
    if train:
        return _CandidateRoute("train", "high", ("explicit_train_request",), True)
    if weather:
        return _CandidateRoute(
            "weather",
            "high",
            ("explicit_weather_request",),
            True,
        )
    if place:
        return _CandidateRoute(
            "place_discovery",
            "high",
            ("explicit_place_discovery",),
            False,
        )
    if explicit_route:
        return _CandidateRoute(
            "mobility_route",
            "high",
            ("explicit_route_task",),
            include_current_date,
        )
    if intercity_relation and english_route_mode_directive_present:
        return _CandidateRoute(
            "clarification_only",
            "low",
            ("insufficient_capability_signal",),
            False,
            resolution_mode="clarification",
        )
    if intercity_relation:
        return _CandidateRoute(
            "mobility_intercity",
            "medium",
            ("origin_destination_relation", "intercity_locations"),
            True,
        )

    # 端点没有地点证据但形状像专名：公开 route_compare 而不是要求澄清。
    # 只公开 schema，不进入 explicit_route，因此计划策略不会强制调用（issue #23）。
    if not request.all_network_denied and signals.route_capability and "route_compare" not in denied_product_requests:
        return _CandidateRoute(
            "mobility_route",
            "medium",
            ("explicit_route_task",),
            include_current_date,
        )
    return None


def _classify_residual_layer(
    request: _RequestSignals,
    english: _EnglishRouteSignals,
) -> _CandidateRoute:
    """所有正向信号都不成立时的兜底；判不出能力族一律要求澄清。"""

    web_search_denied = request.web_search_denied
    explicit_web_search_request = request.explicit_web_search_request
    english_relation = english.relation
    english_abstract_route = english.abstract_route

    if english_relation is not None:
        if english_abstract_route:
            return _CandidateRoute(
                "direct",
                "high",
                ("stable_knowledge_question",),
                False,
            )
        return _CandidateRoute(
            "clarification_only",
            "low",
            ("insufficient_capability_signal",),
            False,
            resolution_mode="clarification",
        )

    if not web_search_denied and explicit_web_search_request:
        return _CandidateRoute(
            "fresh_web",
            "high",
            ("fresh_external_fact",),
            True,
        )

    return _CandidateRoute(
        "clarification_only",
        "low",
        ("insufficient_capability_signal",),
        False,
        resolution_mode="clarification",
    )


def classify_capability_request(
    *,
    message: str,
    task_context_messages: list[object] | None,
    available_tool_names: list[str],
) -> _CandidateRoute:
    """默认的规则式分类器：字面层 → 产品层 → 兜底层，三层各自可单独测试。"""

    request = _extract_request_signals(message)
    literal_route = _classify_literal_layer(request, available_tool_names)
    if literal_route is not None:
        return literal_route

    signals = resolve_product_capability_signals(
        original_message=request.routing_message,
        task_context_messages=task_context_messages,
    )
    if signals.adjacent_route_followup and not request.all_network_denied:
        return _CandidateRoute(
            "mobility_route",
            "high",
            ("adjacent_route_followup",),
            request.include_current_date,
        )

    english = _extract_english_route_signals(request)
    product_route = _classify_product_layer(request, signals, english)
    if product_route is not None:
        return product_route
    return _classify_residual_layer(request, english)


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
    )


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


def _resolve_explicit_authorized_alias(
    message: str,
    available_tool_names: list[str],
) -> str | None:
    product_names = frozenset(_CANONICAL_EXTERNAL_TOOL_ORDER) | _CONTROL_TOOL_NAMES
    aliases = sorted(
        {name for name in available_tool_names if is_authorized_mcp_tool_alias(name) and name not in product_names}
    )
    matched: list[str] = []
    for alias in aliases:
        directives = _explicit_alias_directives(message, alias)
        if directives and directives[-1][2]:
            matched.append(alias)
    return matched[0] if len(matched) == 1 else None


def _explicit_alias_directives(message: str, alias: str) -> list[tuple[int, int, bool]]:
    """返回 alias 出现位置和最后一次显式调用/否定语义，供字面边界共用。"""

    alias_pattern = rf"(?<![A-Za-z0-9_]){re.escape(alias.lower())}(?![A-Za-z0-9_])"
    directives: list[tuple[int, int, bool]] = []
    for alias_match in re.finditer(alias_pattern, message):
        prefix = message[max(0, alias_match.start() - 48) : alias_match.start()]
        if re.search(
            r"(?:不要|不用|别|请勿|禁止|严禁|不得|不可)\s*"
            r"(?:再|随后)?(?:调用|使用|运行|执行)?\s*$",
            prefix,
        ) or re.search(
            r"\b(?:(?:do not|don['’]t|dont|never)\s+(?:call|use|run|invoke)|"
            r"(?:do not|don['’]t|dont|never)\s+execute|"
            r"(?:without|avoid(?:ing)?|refrain\s+from|skip(?:ping)?)\s+"
            r"(?:call(?:ing)?|us(?:e|ing)|run(?:ning)?|invok(?:e|ing)|execut(?:e|ing)))\s+"
            r"(?:the\s+)?(?:mcp\s+)?(?:tool\s+)?$",
            prefix,
            re.IGNORECASE,
        ):
            directives.append((alias_match.start(), alias_match.end(), False))
        elif re.search(
            r"(?:调用|使用|运行|执行)(?:工具)?\s*$|"
            r"\b(?:call|use|run|invoke|execute)\s+(?:the\s+)?(?:mcp\s+)?(?:tool\s+)?$",
            prefix,
            re.IGNORECASE,
        ):
            directives.append((alias_match.start(), alias_match.end(), True))
    return directives


def _normalize_message(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_product_tool_finally_denied(message: str, tool_name: str) -> bool:
    explicit_directives = _explicit_product_tool_directives(message, tool_name)
    if explicit_directives:
        final_explicit = max(explicit_directives, key=lambda event: event[0])
        if not final_explicit[1] and final_explicit[2] is None:
            return True
        if (
            not final_explicit[1]
            and final_explicit[2] is not None
            and _scope_matches_prior_product_request(
                final_explicit[2],
                message[: final_explicit[0]],
            )
        ):
            return True
    natural_directives = [
        (position, allowed, None) for position, allowed in _natural_product_tool_directives(message, tool_name)
    ]
    directives = explicit_directives + natural_directives
    positive_directives = [(position, scope) for position, allowed, scope in directives if allowed]
    filtered_directives = [
        event
        for event in directives
        if event[1]
        or not (
            event[2] is not None
            and any(
                positive_position < event[0] and (positive_scope is None or positive_scope != event[2])
                for positive_position, positive_scope in positive_directives
            )
        )
    ]
    events = [(position, allowed) for position, allowed, _scope in filtered_directives]
    return bool(events and not max(events, key=lambda event: event[0])[1])


def _natural_product_tool_directives(message: str, tool_name: str) -> list[tuple[int, bool]]:
    directives = [
        (match.start(), not _is_natural_product_match_negated(message, match.start()))
        for pattern in _product_tool_positive_patterns(tool_name)
        for match in pattern.finditer(message)
    ]
    if any(allowed for _position, allowed in directives) and _ends_with_product_query_cancellation(message):
        directives.append((len(message) - 1, False))
    return directives


def _ends_with_product_query_cancellation(message: str) -> bool:
    """末句省略领域词时，只撤回本句前已明确请求的产品查询。"""

    boundaries = list(_PRODUCT_DIRECTIVE_BOUNDARY_RE.finditer(message))
    clause = message[boundaries[-1].end() if boundaries else 0 :].strip()
    for prefix in ("算了", "那就", "那", "就"):
        if clause.startswith(prefix):
            clause = clause[len(prefix) :].strip()
            break
    for negative in ("不要", "不用", "请勿", "禁止", "不得", "不可", "别"):
        if not clause.startswith(negative):
            continue
        action = clause[len(negative) :].removeprefix("再")
        return any(
            action == verb + suffix
            for verb in ("查询", "搜索", "查看", "预订", "购买", "查", "找", "看", "订", "买")
            for suffix in ("", "了", "吧", "啦")
        )
    return False


def _final_reauthorized_natural_product_clause(message: str, tool_name: str) -> str | None:
    directives = _natural_product_tool_directives(message, tool_name)
    if not directives:
        return None
    final_position, final_allowed = max(directives, key=lambda event: event[0])
    if not final_allowed or not any(position < final_position and not allowed for position, allowed in directives):
        return None
    prior_boundaries = list(_PRODUCT_DIRECTIVE_BOUNDARY_RE.finditer(message[:final_position]))
    clause_start = prior_boundaries[-1].end() if prior_boundaries else 0
    return message[clause_start:].strip()


def _scope_matches_prior_product_request(scope: str, prior_text: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", scope):
        compact_scope = re.sub(r"[^\w\u4e00-\u9fff]+", "", scope.lower())
        compact_prior = re.sub(r"[^\w\u4e00-\u9fff]+", "", prior_text.lower())
        return len(compact_scope) >= 2 and compact_scope in compact_prior

    def significant_tokens(value: str) -> set[str]:
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower())
        ignored = {
            "a",
            "an",
            "for",
            "from",
            "on",
            "the",
            "to",
            "about",
            "regarding",
            "route",
            "routes",
            "request",
            "task",
            "用于",
            "关于",
            "针对",
        }
        return {token for token in normalized.split() if token not in ignored}

    scope_tokens = significant_tokens(scope)
    if not scope_tokens:
        return False
    prior_tokens = significant_tokens(prior_text)
    return scope_tokens.issubset(prior_tokens)


def _is_natural_product_match_negated(message: str, match_start: int) -> bool:
    prefix = message[max(0, match_start - 48) : match_start]
    prefix = _PRODUCT_DIRECTIVE_BOUNDARY_RE.split(prefix)[-1]
    if re.search(
        r"\bnot\s+(?:only|merely|solely|exclusively)\s+$|"
        r"\b(?:do not|don['’]t|dont)\s+(?:forget\s+to|fail\s+to|just|only|simply)\s+$",
        prefix,
        re.IGNORECASE,
    ):
        return False
    if re.search(r"\b(?:are|is|were|was)\s+there\s+no(?:\s+[\w-]+){0,3}\s+$", prefix, re.IGNORECASE):
        return False
    if re.search(
        r"\b(?:exclude|excluding|avoid|avoiding|skip|skipping|no)\s+"
        r"(?:[\w-]+\s+){1,4}$",
        prefix,
        re.IGNORECASE,
    ) and re.search(
        r"(?:\band\b|\bthen\b|[;；]).{0,96}\b(?:show|find|list|include|prefer|compare)\b"
        r".{0,64}\b(?:options?|alternatives?|connections?|itineraries?)\b",
        message[match_start:],
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:do not|don['’]t|dont|never|not)\s+"
            r"(?:(?!(?:hesitate|forget|fail|just|only|merely|simply)\b)\w+\s+){0,3}$|"
            r"\b(?:avoid|avoiding|without|refrain\s+from|exclude|excluding|skip|skipping)\s+"
            r"(?:\w+\s+){0,3}$|"
            r"\bno(?:\s+need\s+for)?\s+(?:\w+\s+){0,2}$|"
            r"(?:不要|不用|无需|不需要|不必|没必要|没有必要|用不着|别|请勿|禁止|严禁|不得|不可|"
            r"避免|跳过|排除)(?:再)?(?:"
            r"(?:给我|帮我|去|给出|提供|查|查询|搜索|检索|查找|找|查看|获取|推荐|规划|"
            r"比较|对比|预订|订|购买|买)[\w\u4e00-\u9fff\s-]{0,24}"
            r")?\s*$",
            prefix,
            re.IGNORECASE,
        )
    )


def _has_final_positive_product_tool_directive(message: str, tool_name: str) -> bool:
    directives = _explicit_product_tool_directives(message, tool_name)
    positive_directives = [(position, scope) for position, allowed, scope in directives if allowed]
    effective_directives = [
        event
        for event in directives
        if event[1]
        or not (
            event[2] is not None
            and any(
                positive_position < event[0] and (positive_scope is None or positive_scope != event[2])
                for positive_position, positive_scope in positive_directives
            )
        )
    ]
    return bool(effective_directives and max(effective_directives, key=lambda event: event[0])[1])


def _explicit_product_tool_directives(message: str, tool_name: str) -> list[tuple[int, bool, str | None]]:
    escaped_tool_name = re.escape(tool_name)
    negative_pattern = re.compile(
        rf"(?:不要|不用|别|请勿|禁止|严禁|不得|不可).{{0,24}}?(?<![\w]){escaped_tool_name}(?![\w])|"
        rf"\b(?:do not|don['’]t|dont|never)\s+(?:call|use|run|invoke|execute)\s+"
        rf"(?:the\s+)?(?:tool\s+)?{escaped_tool_name}\b(?:\s+tool\b)?|"
        rf"\b(?:without|avoid(?:ing)?|refrain\s+from|skip(?:ping)?)\s+"
        rf"(?:call(?:ing)?|us(?:e|ing)|run(?:ning)?|invok(?:e|ing)|execut(?:e|ing))\s+"
        rf"(?:the\s+)?(?:tool\s+)?{escaped_tool_name}\b(?:\s+tool\b)?",
        re.IGNORECASE,
    )
    positive_tool_pattern = re.compile(
        rf"(?:调用|使用|运行|执行)\s*(?<![\w]){escaped_tool_name}(?![\w])|"
        rf"\b(?:call|use|run|invoke|execute)\s+(?:the\s+)?(?:tool\s+)?{escaped_tool_name}\b(?:\s+tool\b)?",
        re.IGNORECASE,
    )
    negative_matches = list(negative_pattern.finditer(message))
    negative_spans = [match.span() for match in negative_matches]
    events: list[tuple[int, bool, str | None]] = [
        (match.start(), False, _extract_directive_scope(message, match.end())) for match in negative_matches
    ]
    events.extend(
        (match.start(), True, _extract_directive_scope(message, match.end()))
        for match in positive_tool_pattern.finditer(message)
        if not _overlaps_any(match.span(), negative_spans)
    )
    return events


def _product_tool_positive_patterns(tool_name: str) -> tuple[re.Pattern[str], ...]:
    if tool_name == "search_flights":
        return (
            _ZH_FLIGHT_TASK_RE,
            _EN_FLIGHT_TASK_RE,
            re.compile(r"\b(?:by|via)\s+[^?.!,]{0,36}\b(?:plane|air|airplane|flight)\b", re.IGNORECASE),
        )
    if tool_name == "search_trains":
        return (
            _ZH_TRAIN_TASK_RE,
            _EN_TRAIN_TASK_RE,
            re.compile(
                r"\b(?:by|via)\s+[^?.!,]{0,36}\b(?:train|rail|railway|high[- ]speed (?:train|rail))\b",
                re.IGNORECASE,
            ),
        )
    if tool_name == "route_compare":
        return (
            _EN_ROUTE_RELATION_RE,
            re.compile(
                rf"从{_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{1,64}}(?:到|至)"
                rf"{_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{1,64}}(?:怎么|路线|公共交通|地铁|驾车|开车)"
            ),
        )
    if tool_name == "weather_forecast":
        return (_EN_WEATHER_TASK_RE, re.compile(r"天气|气温|温度|下雨|下雪|降雨|降雪|刮风"))
    if tool_name == "local_place_search":
        return (
            _EN_PLACE_TASK_RE,
            re.compile(
                rf"(?:附近|周边){_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,32}}(?:咖啡|餐厅|酒店|景点|店)|"
                rf"(?:找|推荐){_ZH_PRODUCT_CLAUSE_TEXT_ATOM}{{0,32}}(?:咖啡|餐厅|酒店|景点)"
            ),
        )
    return ()


def _is_definitional_knowledge_request(message: str) -> bool:
    noun_definition = _NOUN_DEFINITION_RE.fullmatch(message)
    noun_definition_tail = noun_definition.group("tail").rstrip("?.!") if noun_definition else ""
    noun_definition_term = noun_definition.group("noun").lower() if noun_definition else ""
    if "weather forecast" in noun_definition_term:
        safe_tail_pattern = _SAFE_WEATHER_DEFINITION_TAIL_RE
    elif "current price" in noun_definition_term:
        safe_tail_pattern = _SAFE_CURRENT_PRICE_DEFINITION_TAIL_RE
    elif "source" in noun_definition_term:
        safe_tail_pattern = _SAFE_DEFINITION_TAIL_RE
    else:
        safe_tail_pattern = _SAFE_BASIC_DEFINITION_TAIL_RE
    is_safe_noun_definition = bool(
        noun_definition
        and safe_tail_pattern.fullmatch(noun_definition_tail)
        and not _EXTERNAL_QUERY_DEFINITION_TAIL_RE.search(noun_definition_tail)
    )
    if not is_safe_noun_definition and not _DEFINITIONAL_KNOWLEDGE_RE.search(message):
        return False
    if _URL_RE.search(message):
        return False
    return not bool(
        _POSITIVE_WEB_SEARCH_ACTION_RE.search(message)
        or _POSITIVE_WEB_TOOL_NAME_RE.search(message)
        or _POSITIVE_URL_TOOL_NAME_RE.search(message)
    )


def _extract_english_route_relation(message: str) -> tuple[str, str] | None:
    match = _EN_NATURAL_INTERCITY_RE.search(message) or _EN_ROUTE_RELATION_RE.search(message)
    if match is None:
        return None
    origin = _normalize_english_location(match.group("origin"))
    destination = _normalize_english_location(match.group("destination"))
    if not origin or not destination:
        return None
    return origin, destination


def _normalize_english_location(value: str) -> str:
    normalized = value.strip(" ,.!?").lower()
    normalized = re.sub(
        r"\s+(?:by|via)\s+(?:public transit|train|rail|plane|air|flight|car|bus|taxi|"
        r"walking|cycling|high[- ]speed (?:train|rail))$",
        "",
        normalized,
    )
    return normalized.strip()


def _extract_english_route_modes(message: str) -> frozenset[str]:
    mode_pattern = re.compile(
        r"\b(?:high[- ]speed (?:train|rail|railway)|public transit|"
        r"plane|airplane|flight|"
        r"air(?![- ]condition(?:ed|ing)\b|\s+(?:quality|exposure|pollution|emissions?|pollutants?)\b)|"
        r"train|railway|rail|"
        r"coach|bus|car|taxi|walking|walk|cycling|bike|driving|drive)\b",
        re.IGNORECASE,
    )
    mode_categories = {
        "plane": "flight",
        "airplane": "flight",
        "flight": "flight",
        "air": "flight",
        "train": "train",
        "railway": "train",
        "rail": "train",
        "high-speed train": "train",
        "high speed train": "train",
        "high-speed rail": "train",
        "high speed rail": "train",
        "high-speed railway": "train",
        "high speed railway": "train",
        "public transit": "route",
        "coach": "route",
        "bus": "route",
        "car": "route",
        "taxi": "route",
        "walking": "route",
        "walk": "route",
        "cycling": "route",
        "bike": "route",
        "driving": "route",
        "drive": "route",
    }
    resolved: dict[str, tuple[str, bool]] = {}
    for match in _EN_ROUTE_MODE_SEGMENT_RE.finditer(message):
        context = message[max(0, match.start() - 32) : match.start()] + match.group(0)
        explicit_exclusion_spans = _english_route_mode_exclusion_spans(context)
        for mode_match in mode_pattern.finditer(context):
            prefix = context[: mode_match.start()]
            if mode_match.group(0).lower() == "air" and not re.search(
                r"(?:\b(?:by|via|or|and)|[,/])\s*$",
                prefix,
                re.IGNORECASE,
            ):
                continue
            reset_matches = list(re.finditer(r"\b(?:but|however|then)\b", prefix, re.IGNORECASE))
            scoped_prefix = prefix[reset_matches[-1].end() :] if reset_matches else prefix
            denied = (
                _overlaps_any(mode_match.span(), explicit_exclusion_spans)
                or _is_english_route_mode_denied(scoped_prefix, mode_pattern)
                or bool(
                    re.match(
                        r"\s*(?:\(\s*)?(?:is\s+)?"
                        r"(?:excluded|omitted|skipped|avoided|prohibited|not\s+allowed|off\s+limits)\b",
                        context[mode_match.end() :],
                        re.IGNORECASE,
                    )
                )
            )
            normalized_mode = mode_match.group(0).lower()
            category = mode_categories[normalized_mode]
            state_key = category if category in {"flight", "train"} else normalized_mode
            resolved[state_key] = (category, not denied)
    return frozenset(category for category, allowed in resolved.values() if allowed)


def _english_route_mode_exclusion_spans(context: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in re.finditer(
        r"\b(?:excluding|exclude|avoiding|avoid|skipping|skip|leaving out|leave out|"
        r"omitting|omit|without|except(?: for)?|with the exception of|all but|other than|"
        r"rather than|instead of|as opposed to)\b\s+"
        r"(?P<body>.*?)(?=,\s*by\b|\b(?:but|however|then)\b|$)",
        context,
        re.IGNORECASE,
    ):
        spans.append(match.span("body"))
    for match in re.finditer(
        r"\bwith\s+(?P<body>.*?\b(?:plane|airplane|flight|air|train|railway|rail|"
        r"bus|coach|car|taxi|walking|walk|cycling|bike|driving|drive)\b.*?)\s+"
        r"(?:are\s+)?(?:excluded|omitted|skipped|avoided|prohibited|not\s+allowed|off\s+limits)\b",
        context,
        re.IGNORECASE,
    ):
        spans.append(match.span("body"))
    return spans


def _is_english_route_mode_denied(prefix: str, mode_pattern: re.Pattern[str]) -> bool:
    negative_matches = list(
        re.finditer(
            r"\b(?:rather than|instead of|as opposed to|other than|avoiding|avoid|"
            r"excluding|exclude|skipping|skip|leaving out|leave out|omitting|omit|"
            r"without|except(?: for)?|with the exception of|all but|neither|nor|no)\b|"
            r"\bnot\b(?!\s+only\b)",
            prefix,
            re.IGNORECASE,
        )
    )
    if not negative_matches:
        return False
    tail = prefix[negative_matches[-1].end() :]
    if re.search(r",\s*(?:by|via)\b", tail, re.IGNORECASE):
        return False
    if re.search(r",\s+and\s*$", tail, re.IGNORECASE):
        return False
    tail = mode_pattern.sub(" ", tail)
    tail = re.sub(
        r"\b(?:by|via|or|and|either|both|also|the|a|an|taking|using|traveling|travelling)\b",
        " ",
        tail,
        flags=re.IGNORECASE,
    )
    tail = re.sub(r"[^a-z0-9]+", " ", tail, flags=re.IGNORECASE).strip()
    return not tail


def _is_english_physical_location(value: str) -> bool:
    return (
        is_known_location_name(value)
        or value in EN_KNOWN_LANDMARK_NAMES
        or bool(_EN_PHYSICAL_LOCATION_SUFFIX_RE.search(value))
    )


def _is_english_abstract_route_relation(message: str, origin: str, destination: str) -> bool:
    return bool(
        _EN_ABSTRACT_ROUTE_CONTEXT_RE.search(message)
        or _EN_ABSTRACT_ROUTE_ENDPOINT_RE.search(origin)
        or _EN_ABSTRACT_ROUTE_ENDPOINT_RE.search(destination)
    )
