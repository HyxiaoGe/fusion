"""清理不活动的游客。

游客数据只存在服务端，没有账号可以找回。两类游客连同会话一起删除：
- 打开网站就会领取游客身份，创建超过 GUEST_UNUSED_HOURS 小时仍一个会话都没有的游客；
- 超过 GUEST_INACTIVE_DAYS 天既没有新建也没有更新过会话的游客。
每小时跑一次，多个 worker 用 Redis 锁保证同一小时只跑一份。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.revocation import get_redis
from app.db.database import SessionLocal
from app.db.repositories import UserRepository

logger = logging.getLogger(__name__)

_BATCH_SIZE = 200
_MAX_BATCHES = 50
_LOCK_TTL_SECONDS = 50 * 60
_CHINA_TZ = timezone(timedelta(hours=8))


def _utc_naive(moment: datetime) -> datetime:
    # users.created_at 是不带时区的列：写入的北京时间会被数据库按会话时区（UTC）换算后去掉时区，
    # 实际存的是 UTC 时间。按北京时间比较会让截止点晚 8 小时，游客提前 8 小时被删。
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _purge(session_factory, *, created_before: datetime, active_since: datetime | None) -> int:
    """按条件分批删除游客；每批单独提交，单次最多 _MAX_BATCHES 批。"""

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


def purge_inactive_guests(session_factory=SessionLocal, *, now: datetime | None = None) -> tuple[int, int]:
    """删除不活动的游客，返回（从未使用、长期不活动）各删除的人数。"""

    current = now or datetime.now(timezone.utc)
    unused = _purge(
        session_factory,
        created_before=_utc_naive(current - timedelta(hours=settings.GUEST_UNUSED_HOURS)),
        active_since=None,
    )
    active_since = current - timedelta(days=settings.GUEST_INACTIVE_DAYS)
    inactive = _purge(session_factory, created_before=_utc_naive(active_since), active_since=active_since)
    return unused, inactive


async def purge_inactive_guests_hourly() -> None:
    """定时任务入口：拿到当前小时的锁才执行，失败只记日志。"""

    try:
        hour = datetime.now(_CHINA_TZ).strftime("%Y%m%d%H")
        if not await asyncio.to_thread(get_redis().set, f"guest_cleanup:lock:{hour}", "1", nx=True, ex=_LOCK_TTL_SECONDS):
            return
        unused, inactive = await asyncio.to_thread(purge_inactive_guests)
        logger.info(
            "guest_cleanup_done unused=%s inactive=%s unused_hours=%s inactive_days=%s",
            unused,
            inactive,
            settings.GUEST_UNUSED_HOURS,
            settings.GUEST_INACTIVE_DAYS,
        )
    except Exception:
        logger.warning("guest_cleanup_failed", exc_info=True)
