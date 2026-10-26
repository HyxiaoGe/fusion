"""Credits 额度：准入、结算、窗口用量与管理员重置。

- 准入：开始生成前看日、周两个窗口的已用量，任一用完就拒绝；正在生成、还没结算的回答不计入，
  所以并发或长回答可能略微超出上限，当前回答总能跑完，下一条才拦。管理员与
  CREDITS_EXEMPT_USERNAMES 里的账号不受限，照样记流水。
- 结算：run 进入终态、轨迹账本落定后按实际消耗写一条流水（run_id 唯一，重复结算无副作用）。
  出错结束的 run 不扣，主动停止、触顶等按实际扣。回答任务结束后立即尝试结算，没落定的由
  每分钟一次的补结算兜住（含进程重启丢掉的）。
- 窗口：日窗口为北京时间自然日，周窗口从周一零点算；run 按开始时刻计入窗口。管理员重置写一条
  reset 流水，窗口用量只算最近一次重置之后的消耗。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import AgentSession, CreditLedger, RunTrajectoryMeta
from app.schemas.response import ApiException
from app.services.agent.trajectory_reconciliation import ORPHANED_RUN_ERROR_MESSAGE
from app.services.credit_pricing import compute_run_charge
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

CREDITS_EXHAUSTED_CODE = "CREDITS_EXHAUSTED"
# 游客沿用登录引导的错误码，前端据此弹出登录入口
GUEST_QUOTA_EXHAUSTED_CODE = "GUEST_QUOTA_EXHAUSTED"

TERMINAL_RUN_STATUSES = ("completed", "limit_reached", "incomplete", "interrupted", "error")
# 出错、原因不明的结束不扣：不是用户造成的
UNCHARGED_RUN_STATUSES = frozenset({"error", "incomplete"})

_CHINA_TZ = timezone(timedelta(hours=8))
# 回答结束后等轨迹账本落定的时长；超过后交给补结算
_SETTLE_WAIT_SECONDS = 20.0
_SETTLE_POLL_SECONDS = 1.0
# 补结算：只看最近这么久进入终态的 run；账本迟迟不落定的 run 超过宽限期后按已有数据结算
_SWEEP_LOOKBACK = timedelta(days=2)
_SWEEP_TRAJECTORY_GRACE = timedelta(minutes=10)
_SWEEP_BATCH_SIZE = 200

_background_tasks: set[asyncio.Task] = set()


@dataclass(frozen=True)
class CreditLimits:
    tier: str  # guest | user | admin | exempt
    daily: int | None
    weekly: int | None

    @property
    def unlimited(self) -> bool:
        return self.daily is None and self.weekly is None


def _exempt_usernames() -> frozenset[str]:
    return frozenset(name.strip() for name in settings.CREDITS_EXEMPT_USERNAMES.split(",") if name.strip())


def limits_for(user: Any) -> CreditLimits:
    if getattr(user, "is_superuser", False):
        return CreditLimits("admin", None, None)
    if getattr(user, "username", None) in _exempt_usernames():
        return CreditLimits("exempt", None, None)
    if getattr(user, "is_guest", False):
        return CreditLimits("guest", settings.CREDITS_GUEST_DAILY, settings.CREDITS_GUEST_WEEKLY)
    return CreditLimits("user", settings.CREDITS_USER_DAILY, settings.CREDITS_USER_WEEKLY)


def window_starts(now: datetime | None = None) -> tuple[datetime, datetime]:
    """当前日窗口、周窗口的起点（UTC）。"""
    local = (now or utc_now()).astimezone(_CHINA_TZ)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=local.weekday())
    return day_start.astimezone(timezone.utc), week_start.astimezone(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _last_reset_at(db: Session, user_id: str) -> datetime | None:
    """最近一次对这个账号生效的重置：单独重置或全员重置，取较晚的。"""
    value = db.execute(
        select(func.max(CreditLedger.occurred_at)).where(
            CreditLedger.kind == "reset",
            or_(CreditLedger.user_id == user_id, CreditLedger.user_id.is_(None)),
        )
    ).scalar()
    return _as_utc(value) if value is not None else None


def _used_since(db: Session, user_id: str, since: datetime) -> float:
    value = db.execute(
        select(func.coalesce(func.sum(CreditLedger.credits), 0.0)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.kind == "run",
            CreditLedger.occurred_at >= since,
        )
    ).scalar()
    return float(value or 0.0)


def _window(limit: int | None, used: float, resets_at: datetime) -> dict[str, Any]:
    used = round(used, 2)
    return {
        "limit": limit,
        "used": used,
        "remaining": None if limit is None else max(round(limit - used, 2), 0),
        "resets_at": resets_at.isoformat(),
    }


def usage_summary(db: Session, user: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """用户当前日、周窗口的额度与已用量；不限额的账号 limit/remaining 为 null。"""
    current = now or utc_now()
    limits = limits_for(user)
    day_start, week_start = window_starts(current)
    reset_at = _last_reset_at(db, str(user.id))
    day_since = max(day_start, reset_at) if reset_at else day_start
    week_since = max(week_start, reset_at) if reset_at else week_start
    return {
        "tier": limits.tier,
        "unlimited": limits.unlimited,
        "daily": _window(limits.daily, _used_since(db, str(user.id), day_since), day_start + timedelta(days=1)),
        "weekly": _window(limits.weekly, _used_since(db, str(user.id), week_since), week_start + timedelta(days=7)),
        "last_reset_at": reset_at.isoformat() if reset_at else None,
    }


def ensure_can_start(db: Session, user: Any, *, now: datetime | None = None) -> None:
    """日或周额度用完时拒绝开始新的生成。"""
    limits = limits_for(user)
    if limits.unlimited:
        return
    summary = usage_summary(db, user, now=now)
    # 两个窗口都用完时按周窗口提示，它恢复得更晚
    exhausted = next(
        (
            name
            for name in ("weekly", "daily")
            if summary[name]["limit"] is not None and summary[name]["remaining"] <= 0
        ),
        None,
    )
    if exhausted is None:
        return
    logger.info("credits_exhausted user=%s tier=%s window=%s", user.id, limits.tier, exhausted)
    if limits.tier == "guest":
        when = "今天" if exhausted == "daily" else "本周"
        raise ApiException(GUEST_QUOTA_EXHAUSTED_CODE, f"{when}免登录额度已用完，登录后继续使用", status_code=429)
    message = "今日额度已用完，明天 0 点恢复" if exhausted == "daily" else "本周额度已用完，下周一 0 点恢复"
    raise ApiException(CREDITS_EXHAUSTED_CODE, message, status_code=429)


def requires_stream(user: Any) -> bool:
    """受限账号只能流式生成：非流式回答不经过 Agent 运行，没有可结算的账本。"""
    return not limits_for(user).unlimited


def is_charged_run(run: AgentSession) -> bool:
    """用户造成的结束才扣：正常完成、达到上限、用户停止或发新消息顶替。

    部署、崩溃导致进程退出的运行由后台对账改成中断，这类中断不扣。
    """
    if run.status in UNCHARGED_RUN_STATUSES:
        return False
    return not (run.status == "interrupted" and run.error_message == ORPHANED_RUN_ERROR_MESSAGE)


def _existing_entry(db: Session, run_id: str) -> CreditLedger | None:
    return db.execute(select(CreditLedger).where(CreditLedger.run_id == run_id)).scalar_one_or_none()


def _trajectory_settled(db: Session, run_id: str) -> bool:
    status = db.execute(
        select(RunTrajectoryMeta.trajectory_status).where(RunTrajectoryMeta.run_id == run_id)
    ).scalar_one_or_none()
    return status != "recording"


def settle_run(db: Session, run_id: str, *, force: bool = False) -> CreditLedger | None:
    """按 run 的实际消耗写流水并返回；run 未结束或轨迹账本未落定（force 时不等）返回 None。"""
    existing = _existing_entry(db, run_id)
    if existing is not None:
        return existing
    run = db.get(AgentSession, run_id)
    if run is None or run.status not in TERMINAL_RUN_STATUSES:
        return None
    if not force and not _trajectory_settled(db, run_id):
        return None
    charge = compute_run_charge(db, run_id, fallback_model_id=run.model_id)
    charged = is_charged_run(run)
    entry = CreditLedger(
        user_id=run.user_id,
        kind="run",
        run_id=run_id,
        conversation_id=run.conversation_id,
        message_id=run.message_id,
        model_id=run.model_id,
        run_status=run.status,
        charged=charged,
        credits=charge.total if charged else 0.0,
        breakdown=charge.breakdown(),
        occurred_at=_as_utc(run.created_at),
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        # 另一个 worker 已经结算过
        db.rollback()
        return _existing_entry(db, run_id)
    logger.info(
        "credits_settled run=%s user=%s status=%s credits=%.2f charged=%s",
        run_id,
        run.user_id,
        run.status,
        entry.credits,
        charged,
    )
    return entry


def _settle_in_new_session(run_id: str) -> bool:
    with SessionLocal() as db:
        return settle_run(db, run_id) is not None


async def _settle_when_done(task: asyncio.Task, run_id: str) -> None:
    await asyncio.wait({task})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_WAIT_SECONDS
    while True:
        try:
            if await asyncio.to_thread(_settle_in_new_session, run_id):
                return
        except Exception:  # noqa: BLE001 — 结算失败由补结算重试
            logger.warning("credits_settle_failed run=%s", run_id, exc_info=True)
            return
        if loop.time() >= deadline:
            return
        await asyncio.sleep(_SETTLE_POLL_SECONDS)


def settle_after(task: asyncio.Task, *, run_id: str) -> None:
    """回答任务结束后结算这次 run；挂在后台，不阻塞 SSE。"""
    settle = asyncio.create_task(_settle_when_done(task, run_id))
    _background_tasks.add(settle)
    settle.add_done_callback(_background_tasks.discard)


def settle_pending_runs(session_factory=SessionLocal, *, now: datetime | None = None) -> int:
    """补结算最近进入终态、还没有流水的 run，返回本次结算的条数。"""
    current = now or utc_now()
    settled = 0
    with session_factory() as db:
        rows = db.execute(
            select(AgentSession.id, AgentSession.terminal_at)
            .outerjoin(CreditLedger, CreditLedger.run_id == AgentSession.id)
            .outerjoin(RunTrajectoryMeta, RunTrajectoryMeta.run_id == AgentSession.id)
            .where(
                CreditLedger.id.is_(None),
                AgentSession.status.in_(TERMINAL_RUN_STATUSES),
                AgentSession.terminal_at >= current - _SWEEP_LOOKBACK,
                or_(
                    RunTrajectoryMeta.run_id.is_(None),
                    RunTrajectoryMeta.trajectory_status != "recording",
                    AgentSession.terminal_at < current - _SWEEP_TRAJECTORY_GRACE,
                ),
            )
            .order_by(AgentSession.terminal_at)
            .limit(_SWEEP_BATCH_SIZE)
        ).all()
        for run_id, _ in rows:
            try:
                if settle_run(db, run_id, force=True) is not None:
                    settled += 1
            except Exception:  # noqa: BLE001 — 单条失败不影响其他 run
                db.rollback()
                logger.warning("credits_sweep_settle_failed run=%s", run_id, exc_info=True)
    return settled


async def settle_pending_runs_job() -> None:
    try:
        settled = await asyncio.to_thread(settle_pending_runs)
    except Exception:  # noqa: BLE001
        logger.warning("credits_sweep_failed", exc_info=True)
        return
    if settled:
        logger.info("credits_sweep_settled count=%s", settled)


def reset_user_credits(db: Session, *, user_id: str, actor_id: str, now: datetime | None = None) -> CreditLedger:
    """管理员把用户当前日、周窗口的已用量清零。"""
    entry = CreditLedger(
        user_id=user_id,
        kind="reset",
        charged=False,
        credits=0.0,
        occurred_at=_as_utc(now or utc_now()),
        actor_id=actor_id,
    )
    db.add(entry)
    db.commit()
    logger.info("credits_reset user=%s actor=%s", user_id, actor_id)
    return entry


def reset_all_credits(db: Session, *, actor_id: str, now: datetime | None = None) -> CreditLedger:
    """管理员把所有账号当前日、周窗口的已用量清零：只记一条全员重置，不逐个账号写记录。"""
    entry = CreditLedger(
        user_id=None,
        kind="reset",
        charged=False,
        credits=0.0,
        occurred_at=_as_utc(now or utc_now()),
        actor_id=actor_id,
    )
    db.add(entry)
    db.commit()
    logger.info("credits_reset_all actor=%s", actor_id)
    return entry


def last_reset_all_at(db: Session) -> datetime | None:
    value = db.execute(
        select(func.max(CreditLedger.occurred_at)).where(CreditLedger.kind == "reset", CreditLedger.user_id.is_(None))
    ).scalar()
    return _as_utc(value) if value is not None else None


def serialize_entry(entry: CreditLedger) -> dict[str, Any]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "run_id": entry.run_id,
        "conversation_id": entry.conversation_id,
        "message_id": entry.message_id,
        "model_id": entry.model_id,
        "run_status": entry.run_status,
        "charged": entry.charged,
        "credits": round(entry.credits or 0.0, 2),
        "breakdown": entry.breakdown,
        "occurred_at": _as_utc(entry.occurred_at).isoformat(),
    }


def list_entries(db: Session, *, user_id: str, limit: int = 20, before: datetime | None = None) -> list[CreditLedger]:
    """用户的流水，按时间倒序；before 为上一页最后一条的 occurred_at。"""
    query = select(CreditLedger).where(CreditLedger.user_id == user_id)
    if before is not None:
        query = query.where(CreditLedger.occurred_at < _as_utc(before))
    return list(db.execute(query.order_by(CreditLedger.occurred_at.desc()).limit(limit)).scalars())


def message_entries(db: Session, *, user_id: str, message_id: str) -> list[CreditLedger]:
    """一条回答的结算流水；重新生成、继续生成会产生多个 run。"""
    return list(
        db.execute(
            select(CreditLedger)
            .where(and_(CreditLedger.user_id == user_id, CreditLedger.message_id == message_id))
            .order_by(CreditLedger.occurred_at)
        ).scalars()
    )


def message_credits(db: Session, *, user_id: str, message_id: str) -> dict[str, Any]:
    """一条回答的消耗汇总：已结束还没结算的 run 顺手结算，仍在生成的计入 pending_runs。"""
    run_ids = (
        db.execute(
            select(AgentSession.id).where(AgentSession.message_id == message_id, AgentSession.user_id == user_id)
        )
        .scalars()
        .all()
    )
    pending = sum(1 for run_id in run_ids if settle_run(db, run_id) is None)
    entries = message_entries(db, user_id=user_id, message_id=message_id)
    return {
        "message_id": message_id,
        "credits": round(sum(entry.credits or 0.0 for entry in entries), 2),
        "pending_runs": pending,
        "entries": [serialize_entry(entry) for entry in entries],
    }


def conversation_credits(db: Session, *, user_id: str, conversation_id: str) -> dict[str, Any]:
    """整个对话的消耗：近期结束还没结算的 run 顺手结算，仍在生成的计入 pending_runs。

    只补结算补结算窗口内的 run，打开老对话不会给上线 credits 之前的运行补记流水。
    """
    unsettled = (
        db.execute(
            select(AgentSession.id)
            .outerjoin(CreditLedger, CreditLedger.run_id == AgentSession.id)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.user_id == user_id,
                AgentSession.created_at >= utc_now() - _SWEEP_LOOKBACK,
                CreditLedger.id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    pending = sum(1 for run_id in unsettled if settle_run(db, run_id) is None)
    total, runs = db.execute(
        select(func.coalesce(func.sum(CreditLedger.credits), 0.0), func.count(CreditLedger.id)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.conversation_id == conversation_id,
            CreditLedger.kind == "run",
        )
    ).one()
    return {
        "conversation_id": conversation_id,
        "credits": round(float(total), 2),
        "runs": int(runs),
        "pending_runs": pending,
    }
