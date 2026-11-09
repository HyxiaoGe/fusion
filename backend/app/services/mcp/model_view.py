"""管理页用：按 Agent run 的同一套装配规则，算出每个 MCP 服务此刻实际给模型的工具。

不另写一套判定：直接调用 run 用的 load_mcp_agent_tools，管理页看到的就是下一次普通 run
会公告的工具。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.mcp.agent_tools import load_mcp_agent_tools

_PREVIEW_SCOPE = "admin-model-view"


def build_mcp_model_view(
    db: Any,
    *,
    rows: list[Any],
    load_tools: Callable[..., Any] = load_mcp_agent_tools,
) -> dict[str, Any]:
    tool_set = load_tools(db, user_id=_PREVIEW_SCOPE, conversation_id=_PREVIEW_SCOPE)
    definitions = {definition["function"]["name"]: definition for definition in tool_set.definitions}

    servers: dict[str, dict[str, Any]] = {str(row.id): {"tools": []} for row in rows}
    remote_descriptions = {
        str(row.id): {
            tool["name"]: str(tool.get("description") or "")
            for tool in (row.discovered_tools or [])
            if isinstance(tool, dict) and tool.get("name")
        }
        for row in rows
    }
    for binding in tool_set.audit_bindings:
        server = servers.get(str(binding.get("server_id")))
        alias = binding.get("alias")
        if server is None or alias not in definitions:
            continue
        remote = str(binding.get("remote_tool_name") or "")
        server["tools"].append(
            {
                "name": remote,
                "label": binding.get("tool_label") or alias,
                "description": remote_descriptions.get(str(binding.get("server_id")), {}).get(remote, ""),
            }
        )

    return {"servers": servers}
