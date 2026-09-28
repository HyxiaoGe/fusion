"""Run 能力路由在执行与观测协议间共享的低层安全契约。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

CAPABILITY_CONTROL_TOOL_NAMES = frozenset({"update_plan"})
CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER = (
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
)
# 联网工具只是外部任务的可选替代，不参与主能力可用性和初始调用门禁。
CAPABILITY_RECOVERY_TOOL_NAMES = ("web_search", "url_read")
CAPABILITY_MAX_EXTERNAL_TOOLS = 5
# 同一 MCP 服务的授权工具一起公告，例如 Context7 需要先解析库 ID 再查文档。
CAPABILITY_MAX_MCP_ALIASES = 3

_ON_OFF_PLAN_MODES = frozenset({"on", "off"})
_AUTO_PLAN_MODES = frozenset({"auto", "on", "off"})


@dataclass(frozen=True)
class CapabilityPackageSpec:
    """单个能力包的全部静态语义；路由器、分类器、契约校验与计划门禁都从这里派生。

    新增能力包：在 CAPABILITY_PACKAGES 登记一项，同步 schemas/trajectory.py 的 Literal
    与分类器 Prompt（test_capability_package_registry 校验两者）；前端对未知包原样
    展示，按需补 i18n 文案即可。
    """

    # 固定公告的外部工具（canonical order）；mcp_explicit 为空，运行时由授权别名决定。
    tools: tuple[str, ...]
    # 允许的原因码组合；首项是分类器选中本包时使用的组合。
    reason_code_options: tuple[tuple[str, ...], ...]
    # 允许的置信度；首项是分类器选中本包时使用的置信度。
    confidence_options: tuple[str, ...] = ("high",)
    resolution_mode: str = "routed"
    # 当前日期一律注入（#132）；None 表示随路由上下文变化，契约不校验。
    include_current_date: bool | None = True
    network_boundary_required: bool | None = False
    plan_modes: frozenset[str] = _ON_OFF_PLAN_MODES
    # 模型分类器可直接返回本包；否则只能由服务端模式或降级路径产生。
    model_selectable: bool = True
    # include_current_date 可变时，分类器选中本包的日期标志；None 表示沿用调用方传入值。
    model_include_current_date: bool | None = None
    # 公告工具来自本 Run 授权的 MCP 别名，而非固定工具集。
    mcp_aliases: bool = False
    # 由分类器指定一个必须先调用的主工具（跨产品包没有单一主工具）。
    requires_primary_tool: bool = False
    # 固定工具任一不可用即降级，不允许只公告部分工具。
    requires_all_tools: bool = False

    @property
    def has_external_tools(self) -> bool:
        return bool(self.tools) or self.mcp_aliases

    @property
    def is_product_package(self) -> bool:
        """公告的全部是产品工具（非联网替代），计划门禁要求先调用这些工具。"""

        return bool(self.tools) and not set(self.tools).intersection(CAPABILITY_RECOVERY_TOOL_NAMES)

    def route_include_current_date(self, requested: bool) -> bool:
        if self.include_current_date is not None:
            return self.include_current_date
        if self.model_include_current_date is not None:
            return self.model_include_current_date
        return requested


CAPABILITY_PACKAGES: Mapping[str, CapabilityPackageSpec] = MappingProxyType(
    {
        "direct": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(
                ("stable_knowledge_question",),
                ("direct_greeting",),
                ("assistant_identity_question",),
                ("simple_calculation",),
            ),
        ),
        "transform": CapabilityPackageSpec(tools=(), reason_code_options=(("text_transform_request",),)),
        "date": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(("current_date_question",),),
            include_current_date=True,
        ),
        "fresh_web": CapabilityPackageSpec(
            tools=("web_search",),
            reason_code_options=(("fresh_external_fact",),),
            include_current_date=True,
        ),
        "verified_web": CapabilityPackageSpec(
            tools=("web_search", "url_read"),
            reason_code_options=(("verified_source_request",),),
            include_current_date=True,
            plan_modes=_AUTO_PLAN_MODES,
            requires_all_tools=True,
        ),
        "url_read": CapabilityPackageSpec(tools=("url_read",), reason_code_options=(("explicit_url_read",),)),
        "weather": CapabilityPackageSpec(
            tools=("weather_forecast",),
            reason_code_options=(("explicit_weather_request",),),
            include_current_date=True,
        ),
        "place_discovery": CapabilityPackageSpec(
            tools=("local_place_search",),
            reason_code_options=(("explicit_place_discovery",),),
        ),
        "mobility_route": CapabilityPackageSpec(
            tools=("route_compare",),
            reason_code_options=(("explicit_route_task",), ("adjacent_route_followup",)),
            # 端点未收录、仅凭专名形状放行时以 medium 公开路线工具，不进入强制调用契约。
            confidence_options=("high", "medium"),
            include_current_date=None,
            plan_modes=_AUTO_PLAN_MODES,
        ),
        "flight": CapabilityPackageSpec(
            tools=("search_flights",),
            reason_code_options=(("explicit_flight_request",),),
            include_current_date=True,
        ),
        "train": CapabilityPackageSpec(
            tools=("search_trains",),
            reason_code_options=(("explicit_train_request",),),
            include_current_date=True,
        ),
        "travel_air_rail": CapabilityPackageSpec(
            tools=("search_flights", "search_trains"),
            reason_code_options=(("air_rail_comparison",),),
            include_current_date=True,
            plan_modes=_AUTO_PLAN_MODES,
            requires_all_tools=True,
        ),
        "mobility_intercity": CapabilityPackageSpec(
            tools=("route_compare", "search_flights", "search_trains"),
            reason_code_options=(("origin_destination_relation", "intercity_locations"),),
            confidence_options=("medium",),
            include_current_date=True,
            plan_modes=_AUTO_PLAN_MODES,
            requires_primary_tool=True,
        ),
        "mixed_itinerary": CapabilityPackageSpec(
            tools=("weather_forecast", "local_place_search", "route_compare", "search_flights", "search_trains"),
            reason_code_options=(("mixed_itinerary_request",),),
            include_current_date=True,
            plan_modes=_AUTO_PLAN_MODES,
            requires_primary_tool=True,
        ),
        "deep_research": CapabilityPackageSpec(
            tools=("web_search", "url_read"),
            reason_code_options=(("deep_research_mode",),),
            include_current_date=True,
            plan_modes=frozenset({"on"}),
            model_selectable=False,
        ),
        "knowledge_grounded": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(("knowledge_grounded_mode",),),
            include_current_date=None,
            network_boundary_required=None,
            plan_modes=frozenset({"off"}),
            model_selectable=False,
        ),
        "tools_unavailable": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(
                ("tools_disabled",),
                ("function_calling_unavailable",),
                ("search_capability_unavailable",),
                ("required_tools_unavailable",),
                ("required_skill_unavailable",),
            ),
            confidence_options=("high", "medium"),
            resolution_mode="degraded",
            include_current_date=None,
            network_boundary_required=True,
            plan_modes=frozenset({"off"}),
            model_selectable=False,
        ),
        "clarification_only": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(("insufficient_capability_signal",),),
            confidence_options=("low",),
            resolution_mode="clarification",
        ),
        "mcp_explicit": CapabilityPackageSpec(
            tools=(),
            reason_code_options=(("explicit_authorized_tool_alias",),),
            include_current_date=None,
            model_include_current_date=True,
            mcp_aliases=True,
        ),
    }
)

# 以下均为注册表的派生视图，保留原名供既有调用方使用。
CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES = MappingProxyType(
    {package_id: spec.tools for package_id, spec in CAPABILITY_PACKAGES.items() if not spec.mcp_aliases}
)
CAPABILITY_RECOVERY_PACKAGES = frozenset(
    package_id for package_id, spec in CAPABILITY_PACKAGES.items() if spec.has_external_tools
)
CAPABILITY_MODEL_PACKAGE_IDS = frozenset(
    package_id for package_id, spec in CAPABILITY_PACKAGES.items() if spec.model_selectable
)
CAPABILITY_PRIMARY_TOOL_PACKAGES = frozenset(
    package_id for package_id, spec in CAPABILITY_PACKAGES.items() if spec.requires_primary_tool
)
CAPABILITY_REASON_CODES = frozenset(
    reason_code
    for spec in CAPABILITY_PACKAGES.values()
    for option in spec.reason_code_options
    for reason_code in option
)


@dataclass(frozen=True)
class McpRouteTool:
    """供分类器按任务内容选择 MCP 服务的可信目录项；label 只来自管理员配置。"""

    alias: str
    service_id: str
    label: str


_MCP_TOOL_ALIAS_RE = re.compile(r"mcp_[A-Za-z0-9_-]+")


def is_authorized_mcp_tool_alias(value: object) -> bool:
    """判断工具名是否具备服务端生成的 MCP alias 形状。"""

    return isinstance(value, str) and _MCP_TOOL_ALIAS_RE.fullmatch(value) is not None


def validate_capability_resolution_semantics(
    *,
    package_id: str,
    confidence: str,
    resolution_mode: str,
    reason_codes: Sequence[str],
    external_tool_names: Sequence[str],
    effective_plan_mode: str,
    include_current_date: bool | None,
    network_boundary_required: bool,
    skill_resolution: object | None = None,
) -> None:
    """拒绝无法由能力路由器产生的工具与固定包语义组合。

    include_current_date 传 None 时不校验日期语义：历史 Run 曾按包决定是否注入日期，
    持久化值不能按现行契约回溯判错。
    """

    tool_names = tuple(external_tool_names)
    if CAPABILITY_CONTROL_TOOL_NAMES.intersection(tool_names):
        raise ValueError("能力路由外部工具不得包含内部控制工具")
    if len(tool_names) > CAPABILITY_MAX_EXTERNAL_TOOLS:
        raise ValueError("能力路由最多三个主工具和两个联网替代工具")

    primary_tool_names = tuple(name for name in tool_names if name not in CAPABILITY_RECOVERY_TOOL_NAMES)
    if len(primary_tool_names) > 3:
        raise ValueError("能力路由最多三个主工具和两个联网替代工具")

    spec = CAPABILITY_PACKAGES.get(package_id)
    if spec is None:
        raise ValueError("能力路由包含未知能力包")
    if spec.mcp_aliases:
        aliases = tuple(name for name in tool_names if name not in CAPABILITY_RECOVERY_TOOL_NAMES)
        if not 1 <= len(aliases) <= CAPABILITY_MAX_MCP_ALIASES or not all(
            is_authorized_mcp_tool_alias(alias) for alias in aliases
        ):
            raise ValueError("MCP 能力包必须包含一到三个 mcp_ 授权别名，可附加联网替代工具")
        expected = tuple(name for name in CAPABILITY_RECOVERY_TOOL_NAMES if name in tool_names) + aliases
        if tool_names != expected:
            raise ValueError("能力包外部工具必须使用 canonical order")
    else:
        allowed_tool_names = spec.tools
        if spec.has_external_tools:
            combined = frozenset((*allowed_tool_names, *CAPABILITY_RECOVERY_TOOL_NAMES))
            allowed_tool_names = tuple(name for name in CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER if name in combined)
        actual_tool_names = frozenset(tool_names)
        allowed_tool_name_set = frozenset(allowed_tool_names)
        if not spec.has_external_tools and actual_tool_names:
            raise ValueError("零外部工具能力包不得公告工具")
        if allowed_tool_name_set and not actual_tool_names:
            raise ValueError("外部工具能力包不得缺少全部工具")
        if not actual_tool_names.issubset(allowed_tool_name_set):
            raise ValueError("能力包公告了不属于该包的外部工具")
        canonical_tool_names = tuple(name for name in allowed_tool_names if name in actual_tool_names)
        if tool_names != canonical_tool_names:
            raise ValueError("能力包外部工具必须使用 canonical order")
        if package_id == "deep_research" and actual_tool_names != allowed_tool_name_set:
            raise ValueError("Deep Research 必须公告完整搜索与读取工具集合")

    if effective_plan_mode not in spec.plan_modes:
        raise ValueError("能力包与有效计划模式不匹配")

    if (
        include_current_date is not None
        and spec.include_current_date is not None
        and include_current_date is not spec.include_current_date
    ):
        raise ValueError("能力包与当前日期上下文语义不匹配")

    if spec.network_boundary_required is not None and network_boundary_required is not spec.network_boundary_required:
        raise ValueError("能力包与网络边界语义不匹配")

    if tuple(reason_codes) not in spec.reason_code_options:
        raise ValueError("能力包与路由原因码不匹配")

    if confidence not in spec.confidence_options:
        raise ValueError("能力包与路由置信度不匹配")

    if resolution_mode != spec.resolution_mode:
        raise ValueError("能力包与 resolution mode 不匹配")

    if skill_resolution is None:
        return
    field = (
        skill_resolution.get
        if isinstance(skill_resolution, Mapping)
        else lambda name, default=None: getattr(skill_resolution, name, default)
    )
    status = field("status")
    activation_source = field("activation_source")
    requested_skill_ids = tuple(field("requested_skill_ids", ()) or ())
    skills = tuple(field("skills", ()) or ())
    duration_ms = field("duration_ms")
    error_code = field("error_code")
    if status not in {"not_selected", "loaded", "load_failed"}:
        raise ValueError("Skill 终态非法")
    if activation_source != "capability_package":
        raise ValueError("Skill 激活来源非法")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("Skill 加载耗时非法")
    if (status == "loaded") is not bool(skills):
        raise ValueError("Skill loaded 状态必须与安全元数据一致")
    if status == "loaded":
        skill_ids = tuple(_skill_field(skill, "skill_id") for skill in skills)
        allowed_tool_names = tuple(
            tool_name for skill in skills for tool_name in tuple(_skill_field(skill, "allowed_tool_names", ()) or ())
        )
        if (
            package_id != "verified_web"
            or requested_skill_ids != skill_ids
            or allowed_tool_names != tuple(external_tool_names)
            or error_code is not None
        ):
            raise ValueError("已加载 Skill 必须与能力包和工具权限精确一致")
    elif status == "load_failed":
        if (
            package_id != "tools_unavailable"
            or tuple(reason_codes) != ("required_skill_unavailable",)
            or bool(external_tool_names)
            or not requested_skill_ids
            or error_code != "skill_load_failed"
        ):
            raise ValueError("Skill 加载失败必须 fail closed")
    elif package_id == "verified_web" or requested_skill_ids or skills or error_code is not None:
        raise ValueError("未选择 Skill 时不得携带 Skill 元数据")


def _skill_field(skill: object, name: str, default: object = None) -> object:
    if isinstance(skill, Mapping):
        return skill.get(name, default)
    return getattr(skill, name, default)
