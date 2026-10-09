"""管理页用：按 Agent run 的同一套装配规则，算出每个 MCP 服务此刻实际给模型的工具。

不另写一套判定：直接调用 run 用的 load_mcp_agent_tools 和按需加载判定，管理页看到的
就是下一次普通 run 会公告的工具（deep_research 不公告 MCP，不在这里体现）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.amap_product_tools import (
    AMAP_PRODUCT_REMOTE_DEPENDENCIES,
    AMAP_PRODUCT_REQUIRED_QUOTA_GROUPS,
)
from app.services.mcp.image_service_tools import IMAGE_SERVICE_REMOTE_TOOL_NAME
from app.services.mcp.provider_profiles import is_official_amap_endpoint
from app.services.mcp.provider_quota import read_quota_reset_seconds
from app.services.mcp.tool_deferral import (
    MAX_DIRECT_MCP_SCHEMA_CHARS,
    MAX_DIRECT_MCP_TOOLS,
    mcp_schema_chars,
    should_defer_mcp_tools,
)
from app.utils.run_capability_contract import is_authorized_mcp_tool_alias

_PREVIEW_SCOPE = "admin-model-view"
_ALL_AMAP_QUOTA_GROUPS = frozenset().union(*AMAP_PRODUCT_REQUIRED_QUOTA_GROUPS.values())
_PRODUCT_LABELS = {"local_place_search": "高德地点搜索", "route_compare": "高德路线比较"}


def build_mcp_model_view(
    db: Any,
    *,
    rows: list[Any],
    load_tools: Callable[..., Any] = load_mcp_agent_tools,
    read_quota: Callable[[str, frozenset[str]], dict[str, int]] = read_quota_reset_seconds,
) -> dict[str, Any]:
    quota_by_server = {
        str(row.id): read_quota(str(row.id), _ALL_AMAP_QUOTA_GROUPS)
        for row in rows
        if row.is_enabled and is_official_amap_endpoint(str(row.endpoint_url))
    }
    # 额度只读一次，装配和展示用同一份结论。
    tool_set = load_tools(
        db,
        user_id=_PREVIEW_SCOPE,
        conversation_id=_PREVIEW_SCOPE,
        quota_state_reader=lambda server_id, groups: frozenset(quota_by_server.get(server_id, {})) & groups,
    )
    definitions = {definition["function"]["name"]: definition for definition in tool_set.definitions}
    direct_names = set(tool_set.direct_tool_names)
    generic = [
        definition
        for name, definition in definitions.items()
        if is_authorized_mcp_tool_alias(name) and name not in direct_names
    ]
    on_demand = should_defer_mcp_tools(generic)

    servers: dict[str, dict[str, Any]] = {str(row.id): {"tools": [], "hidden_tools": []} for row in rows}
    for binding in tool_set.audit_bindings:
        server = servers.get(str(binding.get("server_id")))
        alias = binding.get("alias")
        if server is None or alias not in definitions:
            continue
        remote = str(binding.get("remote_tool_name") or "")
        is_product = remote.startswith("product:")
        if is_product:
            source_tools = sorted(AMAP_PRODUCT_REMOTE_DEPENDENCIES.get(alias, {IMAGE_SERVICE_REMOTE_TOOL_NAME}))
        else:
            source_tools = [remote]
        server["tools"].append(
            {
                "name": alias if is_product else remote,
                "label": binding.get("tool_label") or alias,
                "kind": "product" if is_product else "generic",
                "mode": "on_demand" if not is_product and on_demand and alias not in direct_names else "direct",
                "source_tools": source_tools,
            }
        )

    for server_id, quota in quota_by_server.items():
        server = servers[server_id]
        server["quota_exhausted"] = [
            {"group": group, "resets_in_seconds": seconds} for group, seconds in sorted(quota.items())
        ]
        for product, groups in AMAP_PRODUCT_REQUIRED_QUOTA_GROUPS.items():
            exhausted = sorted(groups & set(quota))
            if exhausted:
                server["hidden_tools"].append(
                    {
                        "name": product,
                        "label": _PRODUCT_LABELS.get(product, product),
                        "reason": "quota_exhausted",
                        "resets_in_seconds": max(quota[group] for group in exhausted),
                    }
                )

    return {
        "servers": servers,
        "deferral": {
            "on_demand": on_demand,
            "generic_tool_count": len(generic),
            "max_direct_tools": MAX_DIRECT_MCP_TOOLS,
            "schema_chars": mcp_schema_chars(generic),
            "max_schema_chars": MAX_DIRECT_MCP_SCHEMA_CHARS,
        },
    }
