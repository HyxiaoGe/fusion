"""Run 能力解析在执行与观测协议间共享的低层契约。

能力包分类已删除：普通 Run 把本次可用的全部工具交给模型自行选择，服务端不再预判
用户意图、也不按预判收走工具。这里只剩由用户选择或运行环境决定的几种模式。
"""

from __future__ import annotations

import re

CAPABILITY_CONTROL_TOOL_NAMES = frozenset({"update_plan"})
# 内置外部工具的固定公告顺序；MCP 别名排在其后。
CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER = (
    "web_search",
    "url_read",
    "image_search",
    "video_search",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
    "present_itinerary",
)

DEEP_RESEARCH_TOOL_NAMES = ("web_search", "url_read")

_MCP_TOOL_ALIAS_RE = re.compile(r"mcp_[A-Za-z0-9_-]+")


def is_authorized_mcp_tool_alias(value: object) -> bool:
    """判断工具名是否具备服务端生成的 MCP alias 形状。"""

    return isinstance(value, str) and _MCP_TOOL_ALIAS_RE.fullmatch(value) is not None
