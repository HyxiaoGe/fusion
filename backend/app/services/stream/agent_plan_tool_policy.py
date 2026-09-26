"""按已冻结的能力包约束计划中的产品工具。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.mcp.amap_product_tools import (
    AMAP_LOCAL_PLACE_SEARCH,
    AMAP_ROUTE_COMPARE,
    AMAP_WEATHER_FORECAST,
)
from app.services.mcp.flyai_travel_tools import (
    FLYAI_SEARCH_FLIGHTS,
    FLYAI_SEARCH_TRAINS,
)


@dataclass(frozen=True)
class AgentPlanToolPolicy:
    """服务端对首个模型计划施加的工具契约。"""

    required_initial_tool_counts: dict[str, int] = field(default_factory=dict)
    allowed_tool_names: frozenset[str] | None = None
    reason: str | None = None


# 这些能力包的主工具由模型分类结果确定。出行跨城和混合行程没有单一主工具，
# 因此只公开候选工具，不对计划强制指定调用。
_PRODUCT_PACKAGE_REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    "weather": (AMAP_WEATHER_FORECAST,),
    "place_discovery": (AMAP_LOCAL_PLACE_SEARCH,),
    "mobility_route": (AMAP_ROUTE_COMPARE,),
    "flight": (FLYAI_SEARCH_FLIGHTS,),
    "train": (FLYAI_SEARCH_TRAINS,),
    "travel_air_rail": (FLYAI_SEARCH_FLIGHTS, FLYAI_SEARCH_TRAINS),
}


def resolve_product_package_plan_policy(
    *,
    package_id: str,
    announced_tool_names: list[str],
) -> AgentPlanToolPolicy | None:
    """从能力包与本次实际公开的工具派生计划门禁。"""

    if package_id not in _PRODUCT_PACKAGE_REQUIRED_TOOLS:
        return None
    announced = frozenset(name for name in announced_tool_names if name)
    required = {tool_name: 1 for tool_name in _PRODUCT_PACKAGE_REQUIRED_TOOLS[package_id] if tool_name in announced}
    if not required:
        return None
    return AgentPlanToolPolicy(
        required_initial_tool_counts=required,
        allowed_tool_names=announced,
        reason=f"capability_package:{package_id}",
    )
