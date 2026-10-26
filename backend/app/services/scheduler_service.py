"""
定时任务调度服务

使用 APScheduler 管理定时任务。
在 lifespan startup 时启动，shutdown 时关闭。
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logger import app_logger as logger

_scheduler: AsyncIOScheduler | None = None


async def start_scheduler() -> None:
    """启动定时任务调度器"""
    global _scheduler

    if _scheduler is not None:
        logger.info("定时任务调度器已启动，跳过重复启动")
        return

    from app.services.agent.trajectory_reconciliation import reconcile_trajectory_best_effort
    from app.services.credit_service import settle_pending_runs_job
    from app.services.guest_cleanup import purge_inactive_guests_hourly
    from app.services.inspiration_generator import generate_daily_inspirations_job
    from app.services.notification_service import purge_expired_notifications_daily
    from app.services.user_memory_consolidation import consolidate_user_memories_job

    scheduler = AsyncIOScheduler()

    # 补结算 credits：回答结束时没来得及结算的 run（轨迹账本迟到、进程重启）；run_id 唯一，多 worker 重复跑无副作用
    scheduler.add_job(
        settle_pending_runs_job,
        trigger=IntervalTrigger(seconds=60),
        id="settle_pending_credit_runs",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 每天凌晨按功能目录为首页「今日灵感」出题；多 worker 由任务内的 Redis 锁去重
    scheduler.add_job(
        generate_daily_inspirations_job,
        trigger=CronTrigger(hour=5, minute=0, timezone="Asia/Shanghai"),
        id="generate_daily_inspirations",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        reconcile_trajectory_best_effort,
        trigger=IntervalTrigger(seconds=60),
        id="reconcile_trajectory_ledger",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 每小时清理从未使用和长期不活动的游客；多 worker 由任务内的 Redis 锁去重
    scheduler.add_job(
        purge_inactive_guests_hourly,
        trigger=CronTrigger(minute=30, timezone="Asia/Shanghai"),
        id="purge_inactive_guests",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 每天清理过期站内通知（已读 7 天、未读 30 天）；多 worker 由任务内的 Redis 锁去重
    scheduler.add_job(
        purge_expired_notifications_daily,
        trigger=CronTrigger(hour=4, minute=40, timezone="Asia/Shanghai"),
        id="purge_expired_notifications",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 每 5 分钟整理闲置对话里的用户记忆；多 worker 由任务内的 Redis 锁去重
    scheduler.add_job(
        consolidate_user_memories_job,
        trigger=IntervalTrigger(minutes=5),
        id="consolidate_user_memories",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # provider 健康追踪已迁移到 LiteLLM Proxy，本进程不再做探活

    scheduler.start()
    _scheduler = scheduler
    logger.info("定时任务调度器已启动")


async def stop_scheduler() -> None:
    """关闭定时任务调度器"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("定时任务调度器已关闭")
