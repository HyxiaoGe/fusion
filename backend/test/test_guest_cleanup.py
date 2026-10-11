import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Conversation, User
from app.services import guest_cleanup
from app.services.guest_cleanup import purge_inactive_guests

NOW = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)
CHINA = timezone(timedelta(hours=8))


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _user(db, user_id: str, *, is_guest: bool, days_ago: int) -> None:
    created = (NOW - timedelta(days=days_ago)).astimezone(CHINA).replace(tzinfo=None)
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
        db.commit()

    assert purge_inactive_guests(factory, now=NOW) == 2

    with factory() as db:
        remaining = {user.id for user in db.query(User).all()}
    assert remaining == {"old-active-guest", "new-guest", "old-registered"}


def test_purge_processes_multiple_batches(monkeypatch):
    monkeypatch.setattr(guest_cleanup, "_BATCH_SIZE", 2)
    factory = _session_factory()
    with factory() as db:
        for index in range(5):
            _user(db, f"guest-{index}", is_guest=True, days_ago=40)
        db.commit()

    assert purge_inactive_guests(factory, now=NOW) == 5


class _FakeRedis:
    def __init__(self):
        self.keys: set[str] = set()

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_daily_job_runs_once_per_day_across_workers(monkeypatch):
    redis = _FakeRedis()
    calls = []
    monkeypatch.setattr(guest_cleanup, "get_redis", lambda: redis)
    monkeypatch.setattr(guest_cleanup, "purge_inactive_guests", lambda: calls.append(1) or 0)

    asyncio.run(guest_cleanup.purge_inactive_guests_daily())
    asyncio.run(guest_cleanup.purge_inactive_guests_daily())

    assert calls == [1]


def test_daily_job_swallows_redis_errors(monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(guest_cleanup, "get_redis", broken)
    asyncio.run(guest_cleanup.purge_inactive_guests_daily())
