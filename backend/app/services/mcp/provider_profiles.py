from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.external.agent_tools import agent_tools_url

# Context7 不再直连：经 agent-tools 网关上的代理访问，第三方密钥由代理持有，Fusion 只带产品服务密钥。
CONTEXT7_GATEWAY_SERVICE = "context7"
AGENT_TOOLS_CREDENTIAL_REF = "AGENT_TOOLS_API_KEY"
CONTEXT7_CONNECT_TIMEOUT_FLOOR_SECONDS = 20.0
CONTEXT7_CALL_TIMEOUT_FLOOR_SECONDS = 30.0
CONTEXT7_IDEMPOTENT_TOTAL_TIMEOUT_FLOOR_SECONDS = 45.0
CONTEXT7_READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "resolve-library-id",
        "query-docs",
    }
)

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


def context7_gateway_endpoint() -> str:
    return agent_tools_url(CONTEXT7_GATEWAY_SERVICE, "/mcp")


def _is_agent_tools_endpoint(endpoint_url: str) -> bool:
    base = agent_tools_url("", "").rstrip("/")
    normalized = endpoint_url.strip()
    return normalized == base or normalized.startswith(base + "/")


def endpoint_tool_allowlist(endpoint_url: str) -> frozenset[str] | None:
    """返回指定官方端点的硬白名单；其他 MCP 服务继续使用管理员授权。"""

    if is_official_context7_endpoint(endpoint_url):
        return CONTEXT7_READ_ONLY_TOOL_ALLOWLIST
    return None


def is_official_context7_endpoint(endpoint_url: str) -> bool:
    """Context7 只认网关上的 context7 代理端点；直连 mcp.context7.com 的行不再享有产品化处理。"""

    return endpoint_url.strip().rstrip("/") == context7_gateway_endpoint()


def endpoint_timeout_floors(endpoint_url: str) -> tuple[float, float, float] | None:
    """为已知高延迟官方端点提供最小超时，不放大全部 MCP 的失败等待。"""

    if not is_official_context7_endpoint(endpoint_url):
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

    # 产品服务密钥只能以 Bearer 发给 agent-tools 网关；网关上的任何服务（context7 / image 等）也只接受这一种鉴权。
    if credential_ref == AGENT_TOOLS_CREDENTIAL_REF or _is_agent_tools_endpoint(endpoint_url):
        return (
            _is_agent_tools_endpoint(endpoint_url)
            and auth_type == "bearer"
            and auth_name is None
            and credential_ref == AGENT_TOOLS_CREDENTIAL_REF
        )
    return True


def tool_is_allowed_for_endpoint(endpoint_url: str, tool_name: str) -> bool:
    allowlist = endpoint_tool_allowlist(endpoint_url)
    return allowlist is None or tool_name in allowlist


def endpoint_tool_guidance(endpoint_url: str, tool_name: str) -> str:
    """返回可信的产品级工具使用约束，不采信远端描述作为策略。"""

    if is_official_context7_endpoint(endpoint_url):
        return _CONTEXT7_TOOL_GUIDANCE.get(tool_name, "")
    return ""


def endpoint_tool_schema_override(endpoint_url: str, tool_name: str) -> dict[str, Any] | None:
    """返回 Fusion 维护的可信参数契约，避免官方工具依赖远端可变 schema。"""

    if not is_official_context7_endpoint(endpoint_url):
        return None
    schema = _CONTEXT7_TOOL_SCHEMA_OVERRIDES.get(tool_name)
    return deepcopy(schema) if schema is not None else None
