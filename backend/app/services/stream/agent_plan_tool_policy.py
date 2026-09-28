"""按已冻结的能力包约束计划中的产品工具。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.utils.run_capability_contract import CAPABILITY_PACKAGES


@dataclass(frozen=True)
class AgentPlanToolPolicy:
    """服务端对首个模型计划施加的工具契约。"""

    required_initial_tool_counts: dict[str, int] = field(default_factory=dict)
    allowed_tool_names: frozenset[str] | None = None
    reason: str | None = None


# 单产品能力包的全部工具都要在首个计划中调用。出行跨城和混合行程没有单一主工具，
# 由分类结果指定主工具，不在这里强制。
_PRODUCT_PACKAGE_REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    package_id: spec.tools
    for package_id, spec in CAPABILITY_PACKAGES.items()
    if spec.is_product_package and not spec.requires_primary_tool
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
