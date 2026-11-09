"""地图服务额度用尽：到服务给的恢复时间前不再公告地图工具。

服务按每次上游请求先高德后腾讯，两家都用尽才回 quota_exhausted，并带 reset_at（agent-tools services/maps）。
这里只认这个错误码和 reset_at，不看上游原文。状态写 Redis 供所有 worker 共享，TTL 到 reset_at；
Redis 不可用时按可用处理，不阻断工具注册。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import redis

from app.core.logger import app_logger as logger
from app.utils.time import CHINA_TZ

_KEY = "maps:quota_exhausted:v1"
# 腾讯按天、高德按月恢复；reset_at 读不出或离谱时最多隐藏到这个上限，至少隐藏一分钟。
_MIN_HIDE_SECONDS = 60
_MAX_HIDE_SECONDS = 32 * 24 * 3600

_sync_client: redis.Redis | None = None


def hide_seconds(reset_at: Any, *, now: datetime | None = None) -> int:
    current = now or datetime.now(CHINA_TZ)
    try:
        parsed = datetime.fromisoformat(reset_at) if isinstance(reset_at, str) else None
    except ValueError:
        parsed = None
    if parsed is None:
        return _MAX_HIDE_SECONDS
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    seconds = int((parsed - current).total_seconds())
    return min(max(seconds, _MIN_HIDE_SECONDS), _MAX_HIDE_SECONDS)


async def hide_maps_tools_until(reset_at: Any, *, redis_client: Any = None) -> None:
    client = redis_client
    if client is None:
        from app.core.redis import get_redis_pool

        client = get_redis_pool()
    if client is None:
        return
    seconds = hide_seconds(reset_at)
    try:
        await client.set(_KEY, "1", ex=seconds)
    except Exception as error:  # noqa: BLE001 — 记录失败只影响后续 run 是否隐藏工具
        logger.warning("记录地图额度用尽失败 error_type=%s", type(error).__name__)
        return
    logger.info("地图服务额度用尽，%s 秒内不再公告地图工具", seconds)


def maps_tools_hidden() -> bool:
    client = _get_sync_client()
    if client is None:
        return False
    try:
        return bool(client.get(_KEY))
    except Exception as error:  # noqa: BLE001 — Redis 故障时照常公告
        logger.warning("读取地图额度状态失败 error_type=%s", type(error).__name__)
        return False


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
