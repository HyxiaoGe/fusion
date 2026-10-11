"""清理长期不活动的游客。

游客数据只存在服务端，没有账号可以找回；超过 GUEST_INACTIVE_DAYS 天既没有新建也没有
更新过会话的游客连同会话一起删除。每天跑一次，多个 worker 用 Redis 锁保证同一天只跑一份。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.guest_identity import china_day
from app.core.revocation import get_redis
from app.db.database import SessionLocal
from app.db.repositories import UserRepository

logger = logging.getLogger(__name__)

_BATCH_SIZE = 200
_MAX_BATCHES = 50
_LOCK_TTL_SECONDS = 6 * 3600
_CHINA_TZ = timezone(timedelta(hours=8))


def purge_inactive_guests(session_factory=SessionLocal, *, now: datetime | None = None) -> int:
    """删除不活动的游客，返回删除人数；每批单独提交，单次最多 _MAX_BATCHES 批。"""

    current = now or datetime.now(timezone.utc)
    active_since = current - timedelta(days=settings.GUEST_INACTIVE_DAYS)
    # users.created_at 是不带时区的北京时间
    created_before = active_since.astimezone(_CHINA_TZ).replace(tzinfo=None)
    deleted = 0
    for _ in range(_MAX_BATCHES):
        with session_factory() as db:
            repo = UserRepository(db)
            ids = repo.list_inactive_guest_ids(
                created_before=created_before, active_since=active_since, limit=_BATCH_SIZE
            )
            if not ids:
                break
            deleted += repo.delete_guests(ids)
        if len(ids) < _BATCH_SIZE:
            break
    return deleted


async def purge_inactive_guests_daily() -> None:
    """定时任务入口：拿到当天的锁才执行，失败只记日志。"""

    try:
        lock_key = f"guest_cleanup:lock:{china_day()}"
        if not await asyncio.to_thread(get_redis().set, lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS):
            return
        deleted = await asyncio.to_thread(purge_inactive_guests)
        logger.info("guest_cleanup_done deleted=%s inactive_days=%s", deleted, settings.GUEST_INACTIVE_DAYS)
    except Exception:
        logger.warning("guest_cleanup_failed", exc_info=True)
