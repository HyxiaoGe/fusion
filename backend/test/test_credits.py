"""Credits 计价、结算、窗口用量、准入与接口回归。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import credits as credits_api
from app.api.deps import get_current_admin_user, get_current_user, get_current_user_or_pending_guest, get_db
from app.core.config import settings
from app.db.database import Base
from app.db.models import (
    AgentEvent,
    AgentSession,
    Changelog,
    Conversation,
    CreditLedger,
    CreditResetCard,
    McpServer,
    Notification,
    NotificationUserState,
    RunTrajectoryMeta,
    ToolCallLog,
    User,
)
from app.schemas.response import ApiException
from app.services import credit_pricing, credit_service
from app.services.agent.trajectory_reconciliation import ORPHANED_RUN_ERROR_MESSAGE
from app.services.mcp.tool_contract import build_agent_tool_alias
from app.services.notification_service import _item as notification_item

BJ = timezone(timedelta(hours=8))


def _bj(*args):
    """北京时间转成 UTC：SQLite 存时间会丢时区，写库统一用 UTC。"""
    return datetime(*args, tzinfo=BJ).astimezone(timezone.utc)


# 2026-10-26 是周一
MONDAY_PEAK = _bj(2026, 10, 26, 10, 0)
MONDAY_LUNCH = _bj(2026, 10, 26, 12, 30)

_PER_M = 1e-6
DEEPSEEK_COSTS = {
    "input_cost_per_token": 0.30 * _PER_M,
    "output_cost_per_token": 1.20 * _PER_M,
    "cache_read_input_token_cost": 0.006 * _PER_M,
    "off_peak_pricing": {
        "windows": [
            {"weekdays": [1, 2, 3, 4, 5], "hours_utc": ["10:00-01:00", "04:00-06:00"]},
            {"weekdays": [6, 7], "hours_utc": "00:00-00:00"},
        ],
        "weekday_timezone": "Asia/Shanghai",
        "input_cost_per_token": 0.15 * _PER_M,
        "output_cost_per_token": 0.60 * _PER_M,
        "cache_read_input_token_cost": 0.003 * _PER_M,
    },
}
MIMO_COSTS = {
    "input_cost_per_token": 0.1493 * _PER_M,
    "output_cost_per_token": 0.2985 * _PER_M,
    "cache_read_input_token_cost": 0.002985 * _PER_M,
}
CATALOG = {"deepseek-chat": {"token_costs": DEEPSEEK_COSTS}, "mimo-v2.6-flash": {"token_costs": MIMO_COSTS}}


@pytest.fixture(autouse=True)
def catalog():
    with patch("app.ai.litellm_catalog.get_model_entry", side_effect=lambda model_id: CATALOG.get(model_id)):
        yield


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            AgentSession.__table__,
            AgentEvent.__table__,
            RunTrajectoryMeta.__table__,
            ToolCallLog.__table__,
            McpServer.__table__,
            CreditLedger.__table__,
            CreditResetCard.__table__,
            Changelog.__table__,
            Notification.__table__,
            NotificationUserState.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all(
            [
                User(id="admin", username="admin", is_superuser=True),
                User(id="user-1", username="one"),
                User(id="guest-1", username="guest-1", is_guest=True),
                User(id="eval", username="fusion-eval"),
            ]
        )
        session.flush()
        session.add_all(
            [Conversation(id=f"conv-{uid}", user_id=uid, title="t", model_id="auto") for uid in ("user-1", "guest-1")]
        )
        session.commit()
    return engine


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def _run(
    db,
    run_id,
    *,
    user_id="user-1",
    status="completed",
    started=MONDAY_PEAK,
    trajectory: str | None = "complete",
    message_id="msg-1",
    terminal_at=None,
):
    db.add(
        AgentSession(
            id=run_id,
            conversation_id=f"conv-{user_id}",
            message_id=message_id,
            user_id=user_id,
            model_id="deepseek-chat",
            provider="deepseek",
            status=status,
            created_at=started,
            terminal_at=terminal_at or (started + timedelta(minutes=1) if status != "running" else None),
        )
    )
    if trajectory is not None:
        db.add(RunTrajectoryMeta(run_id=run_id, conversation_id=f"conv-{user_id}", trajectory_status=trajectory))
    db.commit()


def _round(db, run_id, sequence, *, round_id, at, model=None, input_tokens=0, output_tokens=0, cache_read_tokens=0):
    started = {"llm_round_id": round_id}
    if model:
        started["model"] = model
    db.add_all(
        [
            AgentEvent(
                conversation_id="conv-user-1",
                run_id=run_id,
                sequence=sequence,
                event_type="llm_round_started",
                event_ts=at,
                payload=started,
            ),
            AgentEvent(
                conversation_id="conv-user-1",
                run_id=run_id,
                sequence=sequence + 1,
                event_type="llm_round_completed",
                event_ts=at + timedelta(seconds=5),
                payload={
                    "llm_round_id": round_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                },
            ),
        ]
    )
    db.commit()


def _tool(db, run_id, name, status="success"):
    db.add(
        ToolCallLog(
            conversation_id="conv-user-1",
            user_id="user-1",
            tool_name=name,
            status=status,
            model_id="deepseek-chat",
            provider="deepseek",
            trace_id=run_id,
        )
    )
    db.commit()


def _charge(db, user_id, credits, occurred_at, run_id):
    db.add(
        CreditLedger(user_id=user_id, kind="run", run_id=run_id, charged=True, credits=credits, occurred_at=occurred_at)
    )
    db.commit()


def test_settle_prices_each_round_by_its_model_and_start_time_and_counts_successful_tools(db):
    _run(db, "run-1")
    # 高峰时段的 deepseek：600 未命中 + 400 命中缓存，100 输出
    _round(db, "run-1", 1, round_id="r1", at=MONDAY_PEAK, input_tokens=1000, cache_read_tokens=400, output_tokens=100)
    # 换成备用模型 mimo 的一轮
    _round(db, "run-1", 3, round_id="r2", at=MONDAY_PEAK, model="mimo-v2.6-flash", input_tokens=2000, output_tokens=500)
    # 午休空闲时段的 deepseek，按空闲价
    _round(db, "run-1", 5, round_id="r3", at=MONDAY_LUNCH, model="deepseek-chat", input_tokens=2000, output_tokens=500)
    server = McpServer(
        id="srv-amap",
        name="高德地图",
        provider="amap",
        endpoint_url="https://amap.example",
        discovered_tools=[{"name": "maps_direction"}],
    )
    db.add(server)
    db.commit()
    for name, status in [
        ("web_search", "success"),
        ("web_search", "success"),
        ("web_search", "failed"),
        ("url_read", "degraded"),
        (build_agent_tool_alias("srv-amap", "maps_direction"), "success"),
        ("load_skill", "success"),
    ]:
        _tool(db, "run-1", name, status)

    entry = credit_service.settle_run(db, "run-1")

    rounds = entry.breakdown["rounds"]
    assert [item["model"] for item in rounds] == ["deepseek-chat", "mimo-v2.6-flash", "deepseek-chat"]
    assert rounds[0]["credits"] == pytest.approx((600 * 0.30 + 400 * 0.006 + 100 * 1.20) * _PER_M / 0.0001)
    assert rounds[1]["credits"] == pytest.approx((2000 * 0.1493 + 500 * 0.2985) * _PER_M / 0.0001)
    assert rounds[2]["credits"] == pytest.approx((2000 * 0.15 + 500 * 0.60) * _PER_M / 0.0001)
    assert [item["off_peak"] for item in rounds] == [False, False, True]
    tools = {item["tool"]: item for item in entry.breakdown["tools"]}
    assert tools["web_search"]["count"] == 2 and tools["web_search"]["credits"] == 16
    assert tools["url_read"]["credits"] == 5
    assert tools["mcp:amap"]["label"] == "高德地图 / maps_direction" and tools["mcp:amap"]["credits"] == 2
    assert tools["load_skill"]["credits"] == 0
    assert entry.charged is True
    assert entry.credits == pytest.approx(sum(item["credits"] for item in rounds) + 23)
    assert entry.message_id == "msg-1" and entry.occurred_at.replace(tzinfo=timezone.utc) == MONDAY_PEAK


def test_only_user_caused_endings_are_charged(db):
    """出错、原因不明、进程退出后对账改成的中断不扣；用户停止、顶替、达到上限照扣。"""
    cases = {
        "run-error": ("error", None),
        "run-incomplete": ("incomplete", None),
        "run-orphaned": ("interrupted", ORPHANED_RUN_ERROR_MESSAGE),
        "run-stopped": ("interrupted", None),
        "run-regenerated": ("interrupted", "回答已重新生成"),
        "run-limit": ("limit_reached", None),
    }
    for index, (run_id, (status, error_message)) in enumerate(cases.items()):
        _run(db, run_id, status=status, message_id=f"msg-{index}")
        db.get(AgentSession, run_id).error_message = error_message
        _round(db, run_id, 1, round_id=f"r-{run_id}", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)
    db.commit()

    entries = {run_id: credit_service.settle_run(db, run_id) for run_id in cases}

    model_credits = entries["run-error"].breakdown["model_credits"]
    assert model_credits > 0
    for run_id in ("run-error", "run-incomplete", "run-orphaned"):
        assert (entries[run_id].charged, entries[run_id].credits) == (False, 0.0), run_id
        assert entries[run_id].breakdown["model_credits"] == pytest.approx(model_credits)
    for run_id in ("run-stopped", "run-regenerated", "run-limit"):
        assert entries[run_id].charged is True and entries[run_id].credits == pytest.approx(model_credits), run_id


def test_settle_waits_for_run_end_and_trajectory_and_is_idempotent(db):
    _run(db, "run-live", status="running", trajectory="recording")
    assert credit_service.settle_run(db, "run-live") is None
    _run(db, "run-recording", trajectory="recording")
    assert credit_service.settle_run(db, "run-recording") is None

    first = credit_service.settle_run(db, "run-recording", force=True)
    again = credit_service.settle_run(db, "run-recording")

    assert again.id == first.id
    assert db.execute(select(func.count()).select_from(CreditLedger)).scalar() == 1


def test_window_starts_follow_beijing_day_and_monday():
    sunday_night = _bj(2026, 11, 1, 23, 59)
    monday_early = _bj(2026, 11, 2, 0, 30)

    day, week = credit_service.window_starts(sunday_night)
    assert (day.astimezone(BJ), week.astimezone(BJ)) == (
        _bj(2026, 11, 1),
        _bj(2026, 10, 26),
    )
    day, week = credit_service.window_starts(monday_early)
    assert day.astimezone(BJ) == week.astimezone(BJ) == _bj(2026, 11, 2)


def test_usage_counts_daily_and_weekly_windows_and_reset_clears_both(db):
    user = db.get(User, "user-1")
    wednesday = _bj(2026, 10, 28, 15, 0)
    _charge(db, "user-1", 100, _bj(2026, 10, 25, 12, 0), "last-week")
    _charge(db, "user-1", 40, _bj(2026, 10, 27, 9, 0), "tuesday")
    _charge(db, "user-1", 25, _bj(2026, 10, 28, 9, 0), "today")

    summary = credit_service.usage_summary(db, user, now=wednesday)
    assert (summary["daily"]["used"], summary["weekly"]["used"]) == (25, 65)
    assert summary["daily"]["remaining"] == settings.CREDITS_USER_DAILY - 25
    assert summary["daily"]["resets_at"] == _bj(2026, 10, 29).astimezone(timezone.utc).isoformat()
    assert summary["weekly"]["resets_at"] == _bj(2026, 11, 2).astimezone(timezone.utc).isoformat()

    credit_service.reset_user_credits(db, user_id="user-1", actor_id="admin", now=wednesday - timedelta(hours=1))
    _charge(db, "user-1", 7, wednesday - timedelta(minutes=30), "after-reset")
    summary = credit_service.usage_summary(db, user, now=wednesday)
    assert (summary["daily"]["used"], summary["weekly"]["used"]) == (7, 7)
    assert summary["last_reset_at"] is not None


def test_admission_blocks_exhausted_windows_per_tier(db):
    now = _bj(2026, 10, 28, 15, 0)
    with patch.multiple(
        settings,
        CREDITS_GUEST_DAILY=50,
        CREDITS_GUEST_WEEKLY=500,
        CREDITS_USER_DAILY=1000,
        CREDITS_USER_WEEKLY=100,
        CREDITS_EXEMPT_USERNAMES="fusion-eval, other",
    ):
        _charge(db, "guest-1", 50, now - timedelta(hours=1), "g")
        with pytest.raises(ApiException) as guest_error:
            credit_service.ensure_can_start(db, db.get(User, "guest-1"), now=now)
        assert (guest_error.value.code, guest_error.value.status_code) == ("GUEST_QUOTA_EXHAUSTED", 429)
        assert "登录" in guest_error.value.message

        _charge(db, "user-1", 60, now - timedelta(days=1), "u1")
        credit_service.ensure_can_start(db, db.get(User, "user-1"), now=now)
        _charge(db, "user-1", 40, now - timedelta(hours=1), "u2")
        with pytest.raises(ApiException) as user_error:
            credit_service.ensure_can_start(db, db.get(User, "user-1"), now=now)
        assert user_error.value.code == "CREDITS_EXHAUSTED"
        assert "本周" in user_error.value.message
        # 日、周都用完时提示恢复更晚的周窗口
        with patch.object(settings, "CREDITS_USER_DAILY", 40):
            with pytest.raises(ApiException) as both_error:
                credit_service.ensure_can_start(db, db.get(User, "user-1"), now=now)
        assert "本周" in both_error.value.message

        for uid in ("admin", "eval"):
            _charge(db, uid, 10_000, now - timedelta(hours=1), f"big-{uid}")
            credit_service.ensure_can_start(db, db.get(User, uid), now=now)
            summary = credit_service.usage_summary(db, db.get(User, uid), now=now)
            assert summary["unlimited"] is True and summary["daily"]["used"] == 10_000
            assert summary["daily"]["remaining"] is None


def test_sweep_settles_finished_runs_and_waits_for_recording_trajectories(engine, db):
    now = _bj(2026, 10, 26, 15, 0)
    _run(db, "done", terminal_at=now - timedelta(minutes=2))
    _run(db, "no-meta", trajectory=None, terminal_at=now - timedelta(minutes=2))
    _run(db, "recording-fresh", trajectory="recording", terminal_at=now - timedelta(minutes=2))
    _run(db, "recording-stale", trajectory="recording", terminal_at=now - timedelta(minutes=30))
    _run(db, "too-old", terminal_at=now - timedelta(days=3))
    _run(db, "running", status="running", trajectory="recording")

    settled = credit_service.settle_pending_runs(lambda: Session(engine), now=now)

    assert settled == 3
    run_ids = set(db.execute(select(CreditLedger.run_id)).scalars())
    assert run_ids == {"done", "no-meta", "recording-stale"}
    assert credit_service.settle_pending_runs(lambda: Session(engine), now=now) == 0


def test_tool_price_override_and_cost_tiers():
    credit_pricing._tool_prices.cache_clear()
    with patch.object(settings, "CREDITS_TOOL_PRICES", '{"web_search": 12, "mcp:amap": "bad", "new_tool": 3}'):
        prices = credit_pricing.tool_prices()
    assert (prices["web_search"], prices["mcp:amap"], prices["new_tool"]) == (12, 2, 3)

    assert credit_pricing.cost_tier(MIMO_COSTS) == "low"
    assert credit_pricing.cost_tier(DEEPSEEK_COSTS) == "mid"
    assert (
        credit_pricing.cost_tier({"input_cost_per_token": 2 * _PER_M, "output_cost_per_token": 10 * _PER_M}) == "high"
    )
    assert credit_pricing.cost_tier({}) is None


@pytest.fixture
def client(engine):
    app = FastAPI()

    @app.exception_handler(ApiException)
    async def api_error(_request, exc: ApiException):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})

    app.include_router(credits_api.router, prefix="/api/credits")
    app.include_router(credits_api.admin_router, prefix="/api/admin/credits")
    state = SimpleNamespace(user_id="user-1")

    def session():
        with Session(engine) as db:
            yield db

    def current_user():
        with Session(engine) as db:
            return db.get(User, state.user_id)

    def admin_user():
        user = current_user()
        if not user.is_superuser:
            raise ApiException.forbidden("需要管理员权限")
        return user

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_current_user_or_pending_guest] = current_user
    app.dependency_overrides[get_current_admin_user] = admin_user

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "req"
        return await call_next(request)

    return TestClient(app), state


def test_message_credits_settles_finished_runs_and_hides_other_users(client, db):
    test_client, state = client
    _run(db, "run-a", message_id="msg-x", started=datetime.now(timezone.utc))
    _round(db, "run-a", 1, round_id="a1", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)
    _run(db, "run-b", message_id="msg-x", status="running", trajectory="recording", started=datetime.now(timezone.utc))

    data = test_client.get("/api/credits/messages/msg-x").json()["data"]
    assert data["pending_runs"] == 1
    assert [entry["run_id"] for entry in data["entries"]] == ["run-a"]
    assert data["credits"] == pytest.approx(data["entries"][0]["credits"])

    me = test_client.get("/api/credits/me").json()["data"]
    assert me["tier"] == "user" and me["daily"]["used"] == pytest.approx(data["credits"], abs=0.01)
    assert test_client.get("/api/credits/me/entries").json()["data"]["items"][0]["run_id"] == "run-a"

    state.user_id = "guest-1"
    assert test_client.get("/api/credits/messages/msg-x").json()["data"]["entries"] == []


def test_conversation_credits_sum_every_answer_and_skip_runs_before_credits(client, db):
    test_client, state = client
    now = datetime.now(timezone.utc)
    _run(db, "run-a", message_id="msg-x", started=now)
    _round(db, "run-a", 1, round_id="a1", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)
    _run(db, "run-b", message_id="msg-y", started=now)
    _round(db, "run-b", 1, round_id="b1", at=MONDAY_PEAK, input_tokens=2000, output_tokens=200)
    _run(db, "run-live", message_id="msg-z", status="running", trajectory="recording", started=now)
    # credits 上线前的老运行：打开对话不给它补记流水
    _run(db, "run-old", message_id="msg-w", started=now - timedelta(days=30))
    _round(db, "run-old", 1, round_id="o1", at=MONDAY_PEAK, input_tokens=5000, output_tokens=500)

    data = test_client.get("/api/credits/conversations/conv-user-1").json()["data"]
    entries = {
        entry.run_id: entry.credits
        for entry in db.execute(select(CreditLedger).where(CreditLedger.kind == "run")).scalars()
    }
    assert set(entries) == {"run-a", "run-b"}
    assert data["runs"] == 2 and data["pending_runs"] == 1
    assert data["credits"] == pytest.approx(entries["run-a"] + entries["run-b"], abs=0.01)

    state.user_id = "guest-1"
    assert test_client.get("/api/credits/conversations/conv-user-1").json()["data"]["credits"] == 0


def test_only_admin_can_view_and_reset_user_credits(client, db):
    test_client, state = client
    _charge(db, "user-1", 80, datetime.now(timezone.utc), "spent")

    assert test_client.post("/api/admin/credits/users/user-1/reset").status_code == 403
    state.user_id = "admin"
    before = test_client.get("/api/admin/credits/users/user-1").json()["data"]
    assert before["summary"]["daily"]["used"] == 80 and before["items"][0]["run_id"] == "spent"

    after = test_client.post("/api/admin/credits/users/user-1/reset").json()["data"]
    assert after["daily"]["used"] == 0 and after["weekly"]["used"] == 0
    reset = db.execute(select(CreditLedger).where(CreditLedger.kind == "reset")).scalar_one()
    assert (reset.user_id, reset.actor_id) == ("user-1", "admin")
    assert test_client.post("/api/admin/credits/users/missing/reset").status_code == 404


def test_reset_card_clears_both_windows_and_uses_the_earliest_expiring_card(db):
    wednesday = _bj(2026, 10, 28, 15, 0)
    user = db.get(User, "user-1")
    _charge(db, "user-1", 900, wednesday - timedelta(days=1), "tue")
    _charge(db, "user-1", 2900, wednesday - timedelta(hours=2), "wed")
    credit_service.grant_reset_cards(
        db, user=db.get(User, "user-1"), actor_id="admin", count=1, now=wednesday - timedelta(days=20)
    )
    credit_service.grant_reset_cards(
        db, user=db.get(User, "user-1"), actor_id="admin", count=1, now=wednesday - timedelta(days=2)
    )
    # 发放超过 30 天的卡已过期，不计入也不会被用掉
    credit_service.grant_reset_cards(
        db, user=db.get(User, "user-1"), actor_id="admin", count=1, now=wednesday - timedelta(days=31)
    )

    before = credit_service.usage_summary(db, user, now=wednesday)
    assert before["reset_cards"]["available"] == 2
    assert before["reset_cards"]["next_expires_at"] == (wednesday + timedelta(days=10)).isoformat()

    entry = credit_service.use_reset_card(db, user, now=wednesday)
    after = credit_service.usage_summary(db, user, now=wednesday)
    assert (after["daily"]["used"], after["weekly"]["used"]) == (0, 0)
    assert after["reset_cards"]["available"] == 1
    assert after["reset_cards"]["next_expires_at"] == (wednesday + timedelta(days=28)).isoformat()
    used = db.execute(select(CreditResetCard).where(CreditResetCard.used_at.is_not(None))).scalar_one()
    assert used.reset_entry_id == entry.id
    assert credit_service._as_utc(used.granted_at) == wednesday - timedelta(days=20)
    assert credit_service.serialize_entry(entry)["source"] == "reset_card"

    # 窗口不顺延：重置后的消耗照常计入本周，下周一起重新累计
    _charge(db, "user-1", 50, wednesday + timedelta(hours=1), "after")
    assert credit_service.usage_summary(db, user, now=wednesday + timedelta(hours=2))["weekly"]["used"] == 50
    assert credit_service.usage_summary(db, user, now=_bj(2026, 11, 2, 9, 0))["weekly"]["used"] == 0


def test_reset_card_is_refused_without_usage_or_cards_so_repeat_clicks_keep_cards(db):
    wednesday = _bj(2026, 10, 28, 15, 0)
    user = db.get(User, "user-1")
    credit_service.grant_reset_cards(db, user=db.get(User, "user-1"), actor_id="admin", count=2, now=wednesday)

    # 没有用量时用卡没有意义：连点第二次也落在这里，不多扣卡
    with pytest.raises(ApiException, match="没有可重置的用量"):
        credit_service.use_reset_card(db, user, now=wednesday)
    _charge(db, "user-1", 100, wednesday - timedelta(hours=1), "spent")
    credit_service.use_reset_card(db, user, now=wednesday)
    with pytest.raises(ApiException, match="没有可重置的用量"):
        credit_service.use_reset_card(db, user, now=wednesday + timedelta(seconds=1))
    assert credit_service.usage_summary(db, user, now=wednesday)["reset_cards"]["available"] == 1

    guest = db.get(User, "guest-1")
    _charge(db, "guest-1", 100, wednesday - timedelta(hours=1), "guest-spent")
    with pytest.raises(ApiException, match="没有可用的重置卡"):
        credit_service.use_reset_card(db, guest, now=wednesday)
    with pytest.raises(ApiException, match="不限额"):
        credit_service.use_reset_card(db, db.get(User, "admin"), now=wednesday)
    with pytest.raises(ApiException, match="游客不发放"):
        credit_service.grant_reset_cards(db, user=guest, actor_id="admin", count=1, now=wednesday)
    with pytest.raises(ApiException, match="不限额"):
        credit_service.grant_reset_cards(db, user=db.get(User, "admin"), actor_id="admin", count=1, now=wednesday)
    with pytest.raises(ApiException, match="最多发放"):
        credit_service.grant_reset_cards(db, user=db.get(User, "user-1"), actor_id="admin", count=11, now=wednesday)


def test_admin_grants_reset_cards_and_user_spends_one_through_api(client, db):
    test_client, state = client
    _charge(db, "user-1", 80, datetime.now(timezone.utc), "spent")

    assert test_client.post("/api/admin/credits/users/user-1/reset-cards", json={"count": 2}).status_code == 403
    state.user_id = "admin"
    granted = test_client.post("/api/admin/credits/users/user-1/reset-cards", json={"count": 2}).json()["data"]
    assert granted["reset_cards"]["available"] == 2
    assert test_client.post("/api/admin/credits/users/user-1/reset-cards", json={"count": 0}).status_code == 422
    assert test_client.post("/api/admin/credits/users/missing/reset-cards", json={"count": 1}).status_code == 404

    state.user_id = "user-1"
    after = test_client.post("/api/credits/me/reset-card").json()["data"]
    assert after["daily"]["used"] == 0 and after["reset_cards"]["available"] == 1
    assert test_client.post("/api/credits/me/reset-card").status_code == 409
    items = test_client.get("/api/credits/me/entries").json()["data"]["items"]
    assert items[0]["kind"] == "reset" and items[0]["source"] == "reset_card"


def test_grant_all_gives_each_limited_signed_in_account_one_card(client, db):
    test_client, state = client
    db.add(User(id="user-2", username="two"))
    db.commit()

    with patch.object(settings, "CREDITS_EXEMPT_USERNAMES", "fusion-eval"):
        assert test_client.post("/api/admin/credits/reset-cards/grant-all").status_code == 403
        state.user_id = "admin"
        assert test_client.post("/api/admin/credits/reset-cards/grant-all").json()["data"] == {"granted": 2}

    owners = sorted(db.execute(select(CreditResetCard.user_id)).scalars())
    # 游客、管理员、免限额账号不发
    assert owners == ["user-1", "user-2"]
    assert test_client.post("/api/admin/credits/users/guest-1/reset-cards", json={"count": 1}).status_code == 400


def test_reset_all_clears_every_account_without_moving_windows(db):
    wednesday = _bj(2026, 10, 28, 15, 0)
    for uid in ("user-1", "guest-1"):
        _charge(db, uid, 40, wednesday - timedelta(days=1), f"{uid}-tue")
        _charge(db, uid, 30, wednesday - timedelta(hours=2), f"{uid}-wed")

    credit_service.reset_all_credits(db, actor_id="admin", now=wednesday - timedelta(hours=1))
    _charge(db, "user-1", 5, wednesday - timedelta(minutes=30), "after")

    user = credit_service.usage_summary(db, db.get(User, "user-1"), now=wednesday)
    guest = credit_service.usage_summary(db, db.get(User, "guest-1"), now=wednesday)
    assert (user["daily"]["used"], user["weekly"]["used"]) == (5, 5)
    assert (guest["daily"]["used"], guest["weekly"]["used"]) == (0, 0)
    # 窗口照常按自然日和周一计算，不从重置时刻顺延
    assert user["weekly"]["resets_at"] == _bj(2026, 11, 2, 0, 0).astimezone(timezone.utc).isoformat()
    assert user["last_reset_at"] == (wednesday - timedelta(hours=1)).astimezone(timezone.utc).isoformat()

    # 全员重置只写一条，不出现在个人流水里
    resets = db.execute(select(CreditLedger).where(CreditLedger.kind == "reset")).scalars().all()
    assert [(r.user_id, r.actor_id) for r in resets] == [(None, "admin")]
    assert all(e.kind == "run" for e in credit_service.list_entries(db, user_id="user-1", limit=10))

    # 之后单独重置某个账号，取较晚的那次
    credit_service.reset_user_credits(db, user_id="user-1", actor_id="admin", now=wednesday - timedelta(minutes=10))
    assert credit_service.usage_summary(db, db.get(User, "user-1"), now=wednesday)["daily"]["used"] == 0


def test_only_admin_can_reset_all_credits(client, db):
    test_client, state = client
    _charge(db, "user-1", 80, datetime.now(timezone.utc) - timedelta(seconds=5), "spent")

    assert test_client.post("/api/admin/credits/reset-all").status_code == 403
    state.user_id = "admin"
    assert test_client.get("/api/admin/credits/reset-all").json()["data"] == {"last_reset_at": None}

    reset_at = test_client.post("/api/admin/credits/reset-all").json()["data"]["last_reset_at"]
    assert test_client.get("/api/admin/credits/reset-all").json()["data"] == {"last_reset_at": reset_at}
    summary = test_client.get("/api/admin/credits/users/user-1").json()["data"]["summary"]
    assert summary["daily"]["used"] == 0 and summary["last_reset_at"] == reset_at


def _credit_notices(db):
    rows = db.execute(select(Notification).order_by(Notification.user_id, Notification.created_revision)).scalars()
    return [(row.user_id, row.kind, row.title, row.body) for row in rows]


def test_granting_cards_and_resetting_credits_notify_the_affected_users(db):
    wednesday = _bj(2026, 10, 28, 15, 0)
    db.add(User(id="user-2", username="two"))
    db.commit()
    expiry = "可在额度面板中使用，清零今日和本周已用额度，11 月 27 日 15:00 前有效。"

    credit_service.grant_reset_cards(db, user=db.get(User, "user-1"), actor_id="admin", count=2, now=wednesday)
    assert _credit_notices(db) == [("user-1", "reset_cards_granted", "你收到了 2 张额度重置卡", expiry)]

    db.execute(Notification.__table__.delete())
    with patch.object(settings, "CREDITS_EXEMPT_USERNAMES", "fusion-eval"):
        credit_service.grant_reset_cards_to_all(db, actor_id="admin", now=wednesday)
    # 和发卡范围一致：游客、管理员、免限额账号不通知
    assert [(uid, title) for uid, _, title, _ in _credit_notices(db)] == [
        ("user-1", "你收到了 1 张额度重置卡"),
        ("user-2", "你收到了 1 张额度重置卡"),
    ]

    db.execute(Notification.__table__.delete())
    credit_service.reset_user_credits(db, user_id="user-1", actor_id="admin", now=wednesday)
    assert _credit_notices(db) == [("user-1", "credits_reset", "额度已重置", "管理员已清零你今日和本周的已用额度。")]

    db.execute(Notification.__table__.delete())
    with patch.object(settings, "CREDITS_EXEMPT_USERNAMES", "fusion-eval"):
        credit_service.reset_all_credits(db, actor_id="admin", now=wednesday)
    # 游客同样被重置，一起通知；管理员和免限额账号不受额度约束
    notices = _credit_notices(db)
    assert [uid for uid, *_ in notices] == ["guest-1", "user-1", "user-2"]
    assert {(kind, body) for _, kind, _, body in notices} == {
        ("credits_reset", "管理员已为所有用户清零今日和本周的已用额度。")
    }

    # 同一账号多次重置各留一条，点开指向额度面板
    credit_service.reset_user_credits(db, user_id="user-1", actor_id="admin", now=wednesday)
    rows = db.execute(select(Notification).where(Notification.user_id == "user-1")).scalars().all()
    assert len(rows) == 2
    item = notification_item(rows[0])
    assert (item.business_type, item.target.type) == ("credits", "credits")


def test_regenerating_over_dead_run_does_not_charge_it_as_user_superseded(db):
    """进程已退出的运行停在 running；重新生成前要先记为系统中断，否则替换回答时会按「回答已重新生成」扣费。"""
    from app.services.chat_service import ChatService

    _run(db, "run-dead", status="running", message_id="msg-regen")
    _run(db, "run-live", status="running", message_id="msg-live")
    for run_id in ("run-dead", "run-live"):
        _round(db, run_id, 1, round_id=f"r-{run_id}", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)
    service = ChatService.__new__(ChatService)
    service.db = db

    async def run_alive(run_id: str) -> bool:
        return run_id == "run-live"

    with patch("app.services.chat_service.is_run_alive", new=run_alive):
        asyncio.run(service._interrupt_orphaned_message_runs("conv-user-1", "msg-regen"))
        asyncio.run(service._interrupt_orphaned_message_runs("conv-user-1", "msg-live"))

    dead = db.get(AgentSession, "run-dead")
    assert (dead.status, dead.error_message) == ("interrupted", ORPHANED_RUN_ERROR_MESSAGE)
    assert db.get(AgentSession, "run-live").status == "running"
    entry = credit_service.settle_run(db, "run-dead")
    assert (entry.charged, entry.credits) == (False, 0.0)
