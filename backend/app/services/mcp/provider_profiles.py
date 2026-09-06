from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

AMAP_MCP_HOST = "mcp.amap.com"
CONTEXT7_MCP_HOST = "mcp.context7.com"
AMAP_CREDENTIAL_REF = "AMAP_MCP_API_KEY"
CONTEXT7_CREDENTIAL_REF = "CONTEXT7_API_KEY"
CONTEXT7_CONNECT_TIMEOUT_FLOOR_SECONDS = 20.0
CONTEXT7_CALL_TIMEOUT_FLOOR_SECONDS = 30.0
CONTEXT7_IDEMPOTENT_TOTAL_TIMEOUT_FLOOR_SECONDS = 45.0
AMAP_READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "maps_geo",
        "maps_regeocode",
        "maps_weather",
        "maps_direction_bicycling",
        "maps_direction_walking",
        "maps_direction_driving",
        "maps_direction_transit_integrated",
        "maps_distance",
        "maps_text_search",
        "maps_around_search",
        "maps_search_detail",
    }
)
CONTEXT7_READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "resolve-library-id",
        "query-docs",
    }
)

_AMAP_TOOL_GUIDANCE = {
    "maps_geo": render_runtime_prompt("mcp.amap_geo"),
    "maps_regeocode": render_runtime_prompt("mcp.amap_regeocode"),
    "maps_text_search": render_runtime_prompt("mcp.amap_text_search"),
    "maps_around_search": render_runtime_prompt("mcp.amap_around_search"),
    "maps_search_detail": render_runtime_prompt("mcp.amap_search_detail"),
    "maps_distance": render_runtime_prompt("mcp.amap_coordinates"),
    "maps_direction_bicycling": render_runtime_prompt("mcp.amap_coordinates"),
    "maps_direction_walking": render_runtime_prompt("mcp.amap_coordinates"),
    "maps_direction_driving": render_runtime_prompt("mcp.amap_coordinates"),
    "maps_direction_transit_integrated": render_runtime_prompt("mcp.amap_coordinates"),
}
_CONTEXT7_TOOL_GUIDANCE = {
    "resolve-library-id": render_runtime_prompt("mcp.context7_resolve"),
    "query-docs": render_runtime_prompt("mcp.context7_query"),
}
_CONTEXT7_TOOL_SCHEMA_OVERRIDES: dict[str, dict[str, Any]] = {
    "resolve-library-id": {
        "type": "object",
        "properties": {
            "libraryName": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
            },
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
        },
        "required": ["libraryName", "query"],
        "additionalProperties": False,
    },
    "query-docs": {
        "type": "object",
        "properties": {
            "libraryId": {
                "type": "string",
                "minLength": 3,
                "maxLength": 200,
            },
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
        },
        "required": ["libraryId", "query"],
        "additionalProperties": False,
    },
}


def _endpoint_hostname(endpoint_url: str) -> str:
    try:
        return (urlsplit(endpoint_url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def endpoint_tool_allowlist(endpoint_url: str) -> frozenset[str] | None:
    """返回指定官方端点的硬白名单；其他 MCP 服务继续使用管理员授权。"""

    hostname = _endpoint_hostname(endpoint_url)
    if hostname == AMAP_MCP_HOST:
        return AMAP_READ_ONLY_TOOL_ALLOWLIST
    if hostname == CONTEXT7_MCP_HOST:
        return CONTEXT7_READ_ONLY_TOOL_ALLOWLIST
    return None


def is_official_amap_endpoint(endpoint_url: str) -> bool:
    return _endpoint_hostname(endpoint_url) == AMAP_MCP_HOST


def is_official_context7_endpoint(endpoint_url: str) -> bool:
    return _endpoint_hostname(endpoint_url) == CONTEXT7_MCP_HOST


def endpoint_timeout_floors(endpoint_url: str) -> tuple[float, float, float] | None:
    """为已知高延迟官方端点提供最小超时，不放大全部 MCP 的失败等待。"""

    if _endpoint_hostname(endpoint_url) != CONTEXT7_MCP_HOST:
        return None
    return (
        CONTEXT7_CONNECT_TIMEOUT_FLOOR_SECONDS,
        CONTEXT7_CALL_TIMEOUT_FLOOR_SECONDS,
        CONTEXT7_IDEMPOTENT_TOTAL_TIMEOUT_FLOOR_SECONDS,
    )


def endpoint_auth_binding_is_allowed(
    endpoint_url: str,
    *,
    auth_type: str,
    auth_name: str | None,
    credential_ref: str | None,
) -> bool:
    """将官方凭证绑定到固定主机和传输位置，阻止跨提供商误投。"""

    hostname = _endpoint_hostname(endpoint_url)
    if credential_ref == CONTEXT7_CREDENTIAL_REF or hostname == CONTEXT7_MCP_HOST:
        if auth_type == "none":
            return hostname == CONTEXT7_MCP_HOST and auth_name is None and credential_ref is None
        return (
            hostname == CONTEXT7_MCP_HOST
            and auth_type == "header"
            and auth_name == CONTEXT7_CREDENTIAL_REF
            and credential_ref == CONTEXT7_CREDENTIAL_REF
        )
    if credential_ref == AMAP_CREDENTIAL_REF or hostname == AMAP_MCP_HOST:
        return (
            hostname == AMAP_MCP_HOST
            and auth_type == "query"
            and auth_name == "key"
            and credential_ref == AMAP_CREDENTIAL_REF
        )
    return True


def tool_is_allowed_for_endpoint(endpoint_url: str, tool_name: str) -> bool:
    allowlist = endpoint_tool_allowlist(endpoint_url)
    return allowlist is None or tool_name in allowlist


def endpoint_tool_guidance(endpoint_url: str, tool_name: str) -> str:
    """返回可信的产品级工具使用约束，不采信远端描述作为策略。"""

    hostname = _endpoint_hostname(endpoint_url)
    if hostname == AMAP_MCP_HOST:
        return _AMAP_TOOL_GUIDANCE.get(tool_name, "")
    if hostname == CONTEXT7_MCP_HOST:
        return _CONTEXT7_TOOL_GUIDANCE.get(tool_name, "")
    return ""


def endpoint_tool_schema_override(endpoint_url: str, tool_name: str) -> dict[str, Any] | None:
    """返回 Fusion 维护的可信参数契约，避免官方工具依赖远端可变 schema。"""

    if _endpoint_hostname(endpoint_url) != CONTEXT7_MCP_HOST:
        return None
    schema = _CONTEXT7_TOOL_SCHEMA_OVERRIDES.get(tool_name)
    return deepcopy(schema) if schema is not None else None
