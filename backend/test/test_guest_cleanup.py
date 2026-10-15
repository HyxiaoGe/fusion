import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Conversation, User
from app.services import guest_cleanup
from app.services.guest_cleanup import purge_inactive_guests

NOW = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _user(db, user_id: str, *, is_guest: bool, days_ago: int) -> None:
    # 与线上一致：users.created_at 存不带时区的 UTC 时间
    created = (NOW - timedelta(days=days_ago)).astimezone(timezone.utc).replace(tzinfo=None)
    db.add(User(id=user_id, username=user_id, is_guest=is_guest, created_at=created))


def _conversation(db, user_id: str, *, updated_days_ago: int) -> None:
    moment = NOW - timedelta(days=updated_days_ago)
    db.add(
        Conversation(
            id=f"conv-{user_id}",
            user_id=user_id,
            title="t",
            model_id="auto",
            created_at=moment,
            updated_at=moment,
        )
    )


def test_purges_only_inactive_guests():
    factory = _session_factory()
    with factory() as db:
        _user(db, "old-idle-guest", is_guest=True, days_ago=40)
        _user(db, "old-stale-guest", is_guest=True, days_ago=60)
        _user(db, "old-active-guest", is_guest=True, days_ago=60)
        _user(db, "new-guest", is_guest=True, days_ago=3)
        _user(db, "old-registered", is_guest=False, days_ago=400)
        db.flush()
        _conversation(db, "old-stale-guest", updated_days_ago=45)
        _conversation(db, "old-active-guest", updated_days_ago=2)
        _conversation(db, "new-guest", updated_days_ago=3)
        db.commit()

    assert purge_inactive_guests(factory, now=NOW) == (1, 1)

    with factory() as db:
        remaining = {user.id for user in db.query(User).all()}
    assert remaining == {"old-active-guest", "new-guest", "old-registered"}


def _user_hours_ago(db, user_id: str, *, is_guest: bool, hours_ago: int) -> None:
    created = (NOW - timedelta(hours=hours_ago)).astimezone(timezone.utc).replace(tzinfo=None)
    db.add(User(id=user_id, username=user_id, is_guest=is_guest, created_at=created))


def test_purges_guests_unused_for_a_day():
    """只打开过网站、24 小时内没发过消息的游客被清理；有会话的、刚领取的保留。"""

    factory = _session_factory()
    with factory() as db:
        _user_hours_ago(db, "unused-guest", is_guest=True, hours_ago=25)
        _user_hours_ago(db, "fresh-guest", is_guest=True, hours_ago=23)
        _user_hours_ago(db, "chatted-guest", is_guest=True, hours_ago=25)
        _user_hours_ago(db, "unused-registered", is_guest=False, hours_ago=25)
        db.flush()
        _conversation(db, "chatted-guest", updated_days_ago=1)
        db.commit()

    assert purge_inactive_guests(factory, now=NOW) == (1, 0)

    with factory() as db:
        remaining = {user.id for user in db.query(User).all()}
    assert remaining == {"fresh-guest", "chatted-guest", "unused-registered"}


def test_purge_processes_multiple_batches(monkeypatch):
    monkeypatch.setattr(guest_cleanup, "_BATCH_SIZE", 2)
    factory = _session_factory()
    with factory() as db:
        for index in range(5):
            _user(db, f"guest-{index}", is_guest=True, days_ago=40)
        db.commit()

    assert purge_inactive_guests(factory, now=NOW) == (5, 0)


class _FakeRedis:
    def __init__(self):
        self.keys: set[str] = set()

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_job_runs_once_per_hour_across_workers(monkeypatch):
    redis = _FakeRedis()
    calls = []
    monkeypatch.setattr(guest_cleanup, "get_redis", lambda: redis)
    monkeypatch.setattr(guest_cleanup, "purge_inactive_guests", lambda: calls.append(1) or (0, 0))

    asyncio.run(guest_cleanup.purge_inactive_guests_hourly())
    asyncio.run(guest_cleanup.purge_inactive_guests_hourly())

    assert calls == [1]


def test_job_swallows_redis_errors(monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(guest_cleanup, "get_redis", broken)
    asyncio.run(guest_cleanup.purge_inactive_guests_hourly())


def test_create_guest_returns_existing_user_when_created_concurrently():
    from app.db.repositories import UserRepository

    factory = _session_factory()
    with factory() as first, factory() as second:
        created = UserRepository(first).create_guest("guest-race", "游客")
        # 第二个请求在第一个提交前已查过「不存在」，插入撞主键后应返回已建好的记录
        again = UserRepository(second).create_guest("guest-race", "游客")
        assert (created.id, again.id, again.is_guest) == ("guest-race", "guest-race", True)
    with factory() as db:
        assert db.query(User).filter(User.id == "guest-race").count() == 1
