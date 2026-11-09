"""生成图的归属与清理：记录随会话、回答删除，服务里的图在事务提交后删；取图接口按签名 token 和归属。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import generated_images as generated_images_api
from app.api.deps import get_current_user, get_db
from app.db.database import Base
from app.db.models import (
    AgentSession,
    Changelog,
    Conversation,
    GeneratedImage,
    Message,
    Notification,
    NotificationUserState,
    User,
)
from app.db.repositories import ConversationRepository
from app.schemas.chat import GeneratedImageBlock, TextBlock
from app.schemas.response import ApiException
from app.services import generated_images
from app.services.external import agent_tools_images
from app.services.external.agent_tools_images import ImageStoreError
from app.services.stream.persistence import persist_message


def iid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def image_block(n: int) -> dict:
    return {
        "type": "generated_image",
        "id": f"blk-{n}",
        "schema_version": 2,
        "provider": "image",
        "image_id": iid(n),
        "mime_type": "image/png",
        "width": 1024,
        "height": 1024,
        "prompt": f"图 {n}",
    }


@pytest.fixture
def deleted(monkeypatch):
    batches: list[set[str]] = []
    monkeypatch.setattr(generated_images, "schedule_image_deletion", lambda ids: batches.append(set(ids)))
    return batches


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            Message.__table__,
            AgentSession.__table__,
            Changelog.__table__,
            NotificationUserState.__table__,
            Notification.__table__,
            GeneratedImage.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all([User(id="user-1", username="one"), User(id="user-2", username="two")])
        session.flush()
        session.add_all(
            [
                Conversation(id="conv-1", user_id="user-1", title="c1", model_id="m"),
                Conversation(id="conv-2", user_id="user-1", title="c2", model_id="m"),
                Conversation(id="conv-other", user_id="user-2", title="o", model_id="m"),
            ]
        )
        session.flush()
        for n, conversation_id, user_id in [
            (1, "conv-1", "user-1"),
            (2, "conv-1", "user-1"),
            (3, "conv-2", "user-1"),
            (4, "conv-other", "user-2"),
        ]:
            session.add(
                GeneratedImage(id=iid(n), conversation_id=conversation_id, user_id=user_id, mime_type="image/png")
            )
        session.commit()
        yield session


def remaining(db) -> set[str]:
    return {row.id for row in db.query(GeneratedImage).all()}


def test_forgotten_images_are_deleted_from_the_service_only_after_commit(db, deleted):
    generated_images.forget_generated_images(db, {iid(1), "not-an-id"})
    assert deleted == []

    db.commit()

    assert deleted == [{iid(1)}]
    assert remaining(db) == {iid(2), iid(3), iid(4)}
    db.commit()
    assert deleted == [{iid(1)}]


def test_rollback_keeps_records_and_deletes_nothing(db, deleted):
    generated_images.forget_generated_images(db, {iid(1)})
    db.rollback()
    db.commit()

    assert deleted == []
    assert remaining(db) == {iid(1), iid(2), iid(3), iid(4)}


def test_deleting_a_conversation_deletes_only_its_images(db, deleted):
    assert ConversationRepository(db).delete("conv-1", "user-1") is True

    assert deleted == [{iid(1), iid(2)}]
    assert remaining(db) == {iid(3), iid(4)}


def test_deleting_someone_elses_conversation_deletes_nothing(db, deleted):
    assert ConversationRepository(db).delete("conv-other", "user-1") is False

    assert deleted == []
    assert remaining(db) == {iid(1), iid(2), iid(3), iid(4)}


def add_messages(db, *contents):
    rows = []
    for index, (role, content) in enumerate(contents):
        row = Message(
            id=f"msg-{index}",
            conversation_id="conv-1",
            role=role,
            content=content,
            sequence=index,
            created_at=datetime(2026, 11, 1, 0, index, tzinfo=UTC),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def test_editing_a_question_deletes_images_of_the_dropped_replies(db, deleted):
    rows = add_messages(
        db,
        ("user", [{"type": "text", "text": "画一只猫"}]),
        ("assistant", [{"type": "text", "text": "好"}, image_block(1)]),
        ("user", [{"type": "text", "text": "再画一只狗"}]),
        ("assistant", [image_block(2)]),
    )
    conversation = db.get(Conversation, "conv-1")

    ConversationRepository(db).rewrite_user_message_and_truncate(conversation, rows, 2, "画一只鸟")
    db.commit()

    assert deleted == [{iid(2)}]
    assert remaining(db) == {iid(1), iid(3), iid(4)}


def test_regenerated_answer_deletes_images_it_no_longer_shows(db, deleted):
    add_messages(
        db,
        ("user", [{"type": "text", "text": "画两张"}]),
        ("assistant", [image_block(1), image_block(2)]),
    )
    db.get(Message, "msg-1").generation_task_id = "task-1"
    db.commit()

    persisted = persist_message(
        db,
        "msg-1",
        "conv-1",
        "m",
        [TextBlock(type="text", text="重画了"), GeneratedImageBlock(**image_block(2))],
        partial=False,
        generation_task_id="task-1",
        replace_on_success=True,
    )

    assert persisted is True
    assert deleted == [{iid(1)}]
    assert remaining(db) == {iid(2), iid(3), iid(4)}


def test_partial_checkpoint_deletes_nothing(db, deleted):
    add_messages(db, ("user", [{"type": "text", "text": "画"}]), ("assistant", [image_block(1)]))

    persist_message(db, "msg-1", "conv-1", "m", [TextBlock(type="text", text="…")], partial=True)

    assert deleted == []


def test_image_ids_in_blocks_reads_only_generated_image_blocks():
    blocks = [image_block(1), {"type": "file", "image_id": iid(2)}, {"type": "generated_image", "image_id": "x"}, None]

    assert generated_images.image_ids_in_blocks(blocks) == {iid(1)}
    assert generated_images.image_ids_in_blocks(None) == set()


def _client(db, *, user_id="user-1"):
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req-1"
        return await call_next(request)

    @app.exception_handler(ApiException)
    async def handle_api_error(request: Request, error: ApiException):
        return JSONResponse(status_code=error.status_code, content={"code": error.code, "message": error.message})

    app.include_router(generated_images_api.router, prefix="/api/generated-images")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    images = {iid(1): (b"png-bytes", "image/png")}
    calls = []

    async def fetch_image(image_id):
        calls.append(image_id)
        if image_id == iid(2):
            raise ImageStoreError("HTTP 500")
        return images.get(image_id)

    monkeypatch.setattr(generated_images_api, "fetch_image", fetch_image)
    return calls


def test_owner_gets_a_signed_url_and_the_image_through_it(db, store):
    client = _client(db)

    url = client.get(f"/api/generated-images/{iid(1)}/url").json()["data"]["url"]
    response = client.get(url)

    assert url.startswith(f"/api/generated-images/{iid(1)}/content?token=")
    assert response.status_code == 200
    assert response.content == b"png-bytes"
    assert response.headers["content-type"] == "image/png"
    assert "immutable" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_other_users_and_unknown_ids_get_no_url(db, store):
    client = _client(db)

    assert client.get(f"/api/generated-images/{iid(4)}/url").status_code == 404
    assert client.get("/api/generated-images/not-an-id/url").status_code == 404


def test_content_needs_a_token_for_this_image(db, store):
    client = _client(db)
    other_url = generated_images.signed_image_url(iid(3))
    token = other_url.split("token=", 1)[1]

    assert client.get(f"/api/generated-images/{iid(1)}/content?token={token}").status_code == 401
    assert client.get(f"/api/generated-images/{iid(1)}/content?token=bad").status_code == 401
    assert store == []


def test_image_of_a_deleted_conversation_is_gone_even_with_a_token(db, store, deleted):
    client = _client(db)
    url = generated_images.signed_image_url(iid(1))
    ConversationRepository(db).delete("conv-1", "user-1")

    assert client.get(url).status_code == 404
    assert store == []


def test_service_failure_is_a_bad_gateway(db, store):
    # 表里有、服务里没有（如服务被清过）
    db.add(GeneratedImage(id=iid(5), conversation_id="conv-1", user_id="user-1", mime_type="image/png"))
    db.commit()
    client = _client(db)

    assert client.get(generated_images.signed_image_url(iid(2))).status_code == 502
    assert client.get(generated_images.signed_image_url(iid(5))).status_code == 404


def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_image_returns_content_none_or_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        image_id = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path.endswith(f"/image/v1/images/{image_id}")
        assert request.headers["X-Caller-Scene"] == "image_view"
        if image_id == iid(1):
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        if image_id == iid(2):
            return httpx.Response(404)
        if image_id == iid(3):
            return httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})
        return httpx.Response(502)

    async def run():
        transport = _transport(handler)
        assert await agent_tools_images.fetch_image(iid(1), transport=transport) == (b"png", "image/png")
        assert await agent_tools_images.fetch_image(iid(2), transport=transport) is None
        assert await agent_tools_images.fetch_image("../etc/passwd", transport=transport) is None
        for bad in (iid(3), iid(9)):
            with pytest.raises(ImageStoreError):
                await agent_tools_images.fetch_image(bad, transport=transport)

    asyncio.run(run())


def test_fetch_image_rejects_oversized_content(monkeypatch):
    monkeypatch.setattr(agent_tools_images, "MAX_IMAGE_BYTES", 4)
    transport = _transport(lambda request: httpx.Response(200, content=b"12345", headers={"content-type": "image/png"}))

    with pytest.raises(ImageStoreError):
        asyncio.run(agent_tools_images.fetch_image(iid(1), transport=transport))


def test_delete_images_calls_delete_per_id_and_survives_failures():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path.rsplit("/", 1)[-1], request.headers["X-Caller-Scene"]))
        if request.url.path.endswith(iid(1)):
            raise httpx.ConnectError("down")
        return httpx.Response(204)

    agent_tools_images.delete_images([iid(1), "bad", iid(2)], transport=httpx.MockTransport(handler))

    assert seen == [("DELETE", iid(1), "image_delete"), ("DELETE", iid(2), "image_delete")]


def test_background_deletion_is_skipped_without_gateway_key(monkeypatch):
    calls = []
    monkeypatch.setattr(generated_images.settings, "AGENT_TOOLS_API_KEY", "")
    monkeypatch.setattr(generated_images, "delete_images", lambda ids: calls.append(ids))

    generated_images._delete_in_background({iid(1)})

    assert calls == []
