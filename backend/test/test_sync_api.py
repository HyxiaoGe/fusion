"""后台节拍聚合同步：各部分按需返回，单部分失败不拖累其他部分。"""

from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import NotificationUserState, User
from app.schemas.response import ApiException


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = (value, ex)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, NotificationUserState.__table__])
    with Session(engine) as session:
        session.add(User(id="user-1", username="one"))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def viewing_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.services.conversation_viewing.get_redis", lambda: redis)
    return redis


@pytest.fixture
def activity_redis(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.conversation_activity_service.get_redis_pool", lambda: redis)
    return redis


@pytest.fixture
def client(db, viewing_redis, activity_redis):
    from app.api.deps import get_current_user_or_pending_guest, get_db
    from app.api.sync import router

    app = FastAPI()
    app.include_router(router, prefix="/api/sync")

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    @app.exception_handler(ApiException)
    async def handle_api_error(request: Request, error: ApiException):
        return JSONResponse(status_code=error.status_code, content={"code": error.code, "message": error.message})

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_or_pending_guest] = lambda: SimpleNamespace(id="user-1")
    with TestClient(app) as test_client:
        yield test_client


def test_sync_returns_only_requested_sections(client, db, viewing_redis):
    db.add(NotificationUserState(user_id="user-1", revision=7))
    db.commit()

    data = client.post(
        "/api/sync", json={"viewing_conversation_id": "conv-1", "activity": True, "notifications": True}
    ).json()["data"]
    assert data == {
        "viewing": True,
        "activity": {"streaming": []},
        "notifications": {"revision": 7},
        "failed": [],
    }
    assert viewing_redis.values["conversation:viewing:user-1:conv-1"] == ("1", 30)

    data = client.post("/api/sync", json={"notifications": True}).json()["data"]
    assert data == {"viewing": None, "activity": None, "notifications": {"revision": 7}, "failed": []}


def test_sync_notifications_revision_defaults_to_zero_without_state(client):
    data = client.post("/api/sync", json={"notifications": True}).json()["data"]
    assert data["notifications"] == {"revision": 0}


def test_sync_activity_passes_through_service_result(client, monkeypatch):
    async def activity(user_id):
        assert user_id == "user-1"
        return {"streaming": ["conv-1"]}

    monkeypatch.setattr("app.api.sync.get_conversation_activity", activity)
    data = client.post("/api/sync", json={"activity": True}).json()["data"]
    assert data["activity"] == {"streaming": ["conv-1"]}


def test_sync_isolates_section_failures(client, db, monkeypatch):
    def broken_redis():
        raise ConnectionError("redis down")

    async def broken_activity(user_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services.conversation_viewing.get_redis", broken_redis)
    monkeypatch.setattr("app.api.sync.get_conversation_activity", broken_activity)
    db.add(NotificationUserState(user_id="user-1", revision=3))
    db.commit()

    response = client.post(
        "/api/sync", json={"viewing_conversation_id": "conv-1", "activity": True, "notifications": True}
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "viewing": None,
        "activity": None,
        "notifications": {"revision": 3},
        "failed": ["viewing", "activity"],
    }


@pytest.mark.parametrize(
    "payload",
    [{"viewing_conversation_id": "bad id"}, {"viewing_conversation_id": ""}, {"unknown": True}],
)
def test_sync_rejects_invalid_payload(client, payload):
    assert client.post("/api/sync", json=payload).status_code == 422
