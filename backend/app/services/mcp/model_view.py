"""管理页用：按 Agent run 的同一套装配规则，算出每个 MCP 服务此刻实际给模型的工具。

不另写一套判定：直接调用 run 用的 load_mcp_agent_tools，管理页看到的就是下一次普通 run
会公告的工具。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.image_service_tools import IMAGE_SERVICE_REMOTE_TOOL_NAME

_PREVIEW_SCOPE = "admin-model-view"
# 产品工具给模型的定义是英文，管理页预览用这份中文简介。
_PRODUCT_DESCRIPTIONS = {
    "generate_image": "根据文字描述生成图片，可指定画面比例和风格，生成的图片直接显示在回答里。",
}


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
        is_product = remote.startswith("product:")
        server["tools"].append(
            {
                "name": alias if is_product else remote,
                "label": binding.get("tool_label") or alias,
                "kind": "product" if is_product else "generic",
                "source_tools": [IMAGE_SERVICE_REMOTE_TOOL_NAME] if is_product else [remote],
                "description": _PRODUCT_DESCRIPTIONS.get(alias, "")
                if is_product
                else remote_descriptions.get(str(binding.get("server_id")), {}).get(remote, ""),
            }
        )

    return {"servers": servers}
