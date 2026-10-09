"""地图服务商额度用尽状态：用尽的产品工具到额度恢复前不再公告给模型。

高德个人开发者按服务组计月配额（基础搜索服务：关键字/周边/ID 查询等共用 5000 次/月；
基础 LBS 服务：地理编码、路径规划等），用尽时上游返回 USER_DAILY_QUERY_OVER_LIMIT
——错误码虽叫 DAILY，控制台实际是月配额，北京时间每月 1 日零点重置。这里只认服务商
给出的错误码，不按失败次数或文本猜测；状态写 Redis 供所有 worker 共享，TTL 到下月
重置为止。Redis 不可用时一律按可用处理，不阻断工具注册。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import redis

from app.core.logger import app_logger as logger
from app.services.mcp.client import McpClientError

_QUOTA_RESET_TZ = ZoneInfo("Asia/Shanghai")
_RESET_MARGIN_SECONDS = 60
_KEY_PREFIX = "mcp:quota_exhausted:v1"
_AMAP_DAILY_QUOTA_ERROR = re.compile(r"\b(?:USER_DAILY_QUERY_OVER_LIMIT|DAILY_QUERY_OVER_LIMIT)\b")
# 高德搜索类接口共用一份月配额。
_AMAP_SEARCH_QUOTA_TOOLS = frozenset({"maps_text_search", "maps_around_search", "maps_search_detail"})
AMAP_SEARCH_QUOTA_GROUP = "search"

_sync_client: redis.Redis | None = None


def amap_quota_group(remote_tool_name: str) -> str:
    return AMAP_SEARCH_QUOTA_GROUP if remote_tool_name in _AMAP_SEARCH_QUOTA_TOOLS else remote_tool_name


def is_daily_quota_exhausted(error: McpClientError) -> bool:
    """只认高德返回的额度用尽错误码；QPS 超限等瞬时错误不算。"""

    if error.code != "tool_error":
        return False
    details = getattr(error, "safe_details", None)
    message = details.get("upstream_message") if isinstance(details, dict) else None
    return isinstance(message, str) and _AMAP_DAILY_QUOTA_ERROR.search(message) is not None


def seconds_until_quota_reset(now: datetime | None = None) -> int:
    current = (now or datetime.now(_QUOTA_RESET_TZ)).astimezone(_QUOTA_RESET_TZ)
    month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_reset = (month_start + timedelta(days=32)).replace(day=1)
    return int((next_reset - current).total_seconds()) + _RESET_MARGIN_SECONDS


def _key(server_id: str, quota_group: str) -> str:
    return f"{_KEY_PREFIX}:{server_id}:{quota_group}"


async def mark_quota_exhausted(server_id: str, quota_group: str, *, redis_client: Any = None) -> None:
    client = redis_client
    if client is None:
        from app.core.redis import get_redis_pool

        client = get_redis_pool()
    if client is None:
        return
    try:
        await client.set(_key(server_id, quota_group), "1", ex=seconds_until_quota_reset())
    except Exception as error:  # noqa: BLE001 — 记录失败只影响后续 run 是否隐藏工具
        logger.warning("记录 MCP 额度用尽失败 server_id=%s error_type=%s", server_id, type(error).__name__)
        return
    logger.info("MCP 额度用尽，下月重置前隐藏依赖该额度的工具 server_id=%s quota=%s", server_id, quota_group)


def _get_sync_client() -> redis.Redis | None:
    global _sync_client
    if _sync_client is None:
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            return None
        _sync_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    return _sync_client


def read_exhausted_quota_groups(server_id: str, quota_groups: frozenset[str]) -> frozenset[str]:
    """返回当前已用尽的额度组；读不到时按全部可用处理。"""

    client = _get_sync_client()
    if client is None or not quota_groups:
        return frozenset()
    groups = sorted(quota_groups)
    try:
        values = client.mget([_key(server_id, group) for group in groups])
    except Exception as error:  # noqa: BLE001 — Redis 故障时不隐藏任何工具
        logger.warning("读取 MCP 额度状态失败 server_id=%s error_type=%s", server_id, type(error).__name__)
        return frozenset()
    return frozenset(group for group, value in zip(groups, values, strict=True) if value)


def read_quota_reset_seconds(server_id: str, quota_groups: frozenset[str]) -> dict[str, int]:
    """管理页用：已用尽的额度组及距离重置的秒数；读不到时返回空。"""

    client = _get_sync_client()
    if client is None or not quota_groups:
        return {}
    groups = sorted(quota_groups)
    try:
        ttls = [client.ttl(_key(server_id, group)) for group in groups]
    except Exception as error:  # noqa: BLE001 — 只影响展示
        logger.warning("读取 MCP 额度状态失败 server_id=%s error_type=%s", server_id, type(error).__name__)
        return {}
    # ttl：-2 表示没有标记，-1 表示没有过期时间（不应出现，按仍用尽处理）。
    return {group: max(int(ttl), 0) for group, ttl in zip(groups, ttls, strict=True) if ttl != -2}
