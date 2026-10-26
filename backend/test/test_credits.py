"""Credits 计价、结算、窗口用量、准入与接口回归。"""

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
    Conversation,
    CreditLedger,
    McpServer,
    RunTrajectoryMeta,
    ToolCallLog,
    User,
)
from app.schemas.response import ApiException
from app.services import credit_pricing, credit_service
from app.services.mcp.tool_contract import build_agent_tool_alias

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


def test_error_run_keeps_breakdown_without_charging_but_cancelled_run_is_charged(db):
    _run(db, "run-error", status="error")
    _round(db, "run-error", 1, round_id="e1", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)
    _run(db, "run-stopped", status="interrupted")
    _round(db, "run-stopped", 1, round_id="s1", at=MONDAY_PEAK, input_tokens=1000, output_tokens=100)

    failed = credit_service.settle_run(db, "run-error")
    stopped = credit_service.settle_run(db, "run-stopped")

    assert (failed.charged, failed.credits) == (False, 0.0)
    assert failed.breakdown["model_credits"] > 0
    assert stopped.charged is True and stopped.credits == pytest.approx(failed.breakdown["model_credits"])


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
