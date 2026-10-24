"""
热榜客户端：经 agent-tools 网关的 TrendRadar MCP 读取最新一批热榜标题。

TrendRadar 每 30 分钟抓一次国内主流热榜；这里只取标题和平台名，供后台任务出题参考。
失败直接抛出，由调用方决定怎么处理，不拿空列表冒充「今天没有热点」。
"""

from __future__ import annotations

import json

from app.services.external.agent_tools import agent_tools_headers, agent_tools_url
from app.services.mcp.client import StreamableHttpMcpConnector

_CONNECT_TIMEOUT_SECONDS = 5
_CALL_TIMEOUT_SECONDS = 30


class TrendHeadlinesError(Exception):
    """热榜服务不可用或返回了无法使用的数据。"""


async def fetch_latest_headlines(*, limit: int, scene: str) -> list[str]:
    """返回去重后的「标题（平台名）」列表，按热榜返回顺序（各平台排名靠前的在前）。"""

    async with StreamableHttpMcpConnector().connect(
        endpoint_url=agent_tools_url("trends", "/mcp"),
        headers=agent_tools_headers(scene),
        query_params={},
        connect_timeout_seconds=_CONNECT_TIMEOUT_SECONDS,
        call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
    ) as session:
        await session.initialize()
        result = await session.call_tool("get_latest_news", {"limit": limit})

    text = "".join(getattr(block, "text", "") for block in result.content or [])
    if result.isError:
        raise TrendHeadlinesError(f"热榜工具返回错误：{text[:200]}")
    return parse_headlines(text)


def parse_headlines(text: str) -> list[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TrendHeadlinesError("热榜返回的不是 JSON") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        raise TrendHeadlinesError("热榜返回格式不符合预期")

    headlines: list[str] = []
    seen: set[str] = set()
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        platform = str(item.get("platform_name") or "").strip()
        headlines.append(f"{title}（{platform}）" if platform else title)
    if not headlines:
        raise TrendHeadlinesError("热榜没有可用标题")
    return headlines
