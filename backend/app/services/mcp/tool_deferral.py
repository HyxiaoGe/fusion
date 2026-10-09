"""通用 MCP 工具直接公告还是改由 tool_search 按需加载的统一判定。

Agent run 组装工具列表和管理页展示模型可见工具都用这里，保证两处结论一致。
"""

from __future__ import annotations

import json

# 授权 MCP 工具超过任一阈值时改由 tool_search 按需加载，避免每轮工具列表过长稀释模型注意力。
MAX_DIRECT_MCP_TOOLS = 10
MAX_DIRECT_MCP_SCHEMA_CHARS = 20000


def mcp_schema_chars(mcp_tools: list[dict]) -> int:
    return sum(len(json.dumps(tool, ensure_ascii=False)) for tool in mcp_tools)


def should_defer_mcp_tools(mcp_tools: list[dict]) -> bool:
    """MCP 工具过多时改为按需加载；只看数量和体积，不看用户意图。"""

    if len(mcp_tools) > MAX_DIRECT_MCP_TOOLS:
        return True
    return mcp_schema_chars(mcp_tools) > MAX_DIRECT_MCP_SCHEMA_CHARS
