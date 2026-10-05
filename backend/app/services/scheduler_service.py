"""
定时任务调度服务

使用 APScheduler 管理定时任务。
在 lifespan startup 时启动，shutdown 时关闭。
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
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

    scheduler = AsyncIOScheduler()

    # 首页示例问题由 dev 每日探针出题后写入（store_prompt_examples），这里不再定时生成

    scheduler.add_job(
        reconcile_trajectory_best_effort,
        trigger=IntervalTrigger(seconds=60),
        id="reconcile_trajectory_ledger",
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
