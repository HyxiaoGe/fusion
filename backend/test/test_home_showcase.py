import asyncio
import io
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import prompts
from app.api.deps import get_admin_audit_service, get_current_admin_user, get_file_service
from app.db.database import get_db
from app.db.models import Conversation, HomeShowcaseImage, Message, User
from app.services import home_showcase_service


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, key, data, content_type):
        self.objects[key] = data
        return key

    async def download(self, key):
        return self.objects[key]

    async def delete(self, key):
        return self.objects.pop(key, None) is not None


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(home_showcase_service, "get_storage", lambda: fake)
    monkeypatch.setattr(home_showcase_service, "get_storage_for_backend", lambda _backend: fake)
    return fake


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Conversation, Message, HomeShowcaseImage):
        model.__table__.create(engine)
    session = sessionmaker(autoflush=False, bind=engine)()
    yield session
    session.close()


def _png(width, height):
    output = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(output, format="PNG")
    return output.getvalue()


def _add(db, **overrides):
    values = {
        "content": _png(1024, 1536),
        "label": "水彩插画",
        "prompt": "画一只水彩风格的橘猫",
        "object_position": None,
        "created_by": "admin-1",
    }
    values.update(overrides)
    return asyncio.run(home_showcase_service.add_showcase_image(db, **values))


def test_add_converts_to_webp_with_480_short_side(db, storage):
    row = _add(db, object_position="top")

    assert (row.width, row.height) == (480, 720)
    stored = Image.open(io.BytesIO(storage.objects[row.storage_key]))
    assert stored.format == "WEBP" and stored.size == (480, 720)
    assert home_showcase_service.serialize_showcase_image(row) == {
        "id": row.id,
        "label": "水彩插画",
        "prompt": "画一只水彩风格的橘猫",
        "object_position": "top",
        "source_file_id": None,
        "image_url": f"/api/prompts/showcase/{row.id}/image",
        "width": 480,
        "height": 720,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"label": "  "},
        {"label": "超" * 13},
        {"prompt": ""},
        {"prompt": "长" * 1001},
        {"object_position": "left"},
        {"content": b"not an image"},
    ],
)
def test_add_rejects_invalid_input_without_storing(db, storage, overrides):
    with pytest.raises(home_showcase_service.ShowcaseImageError):
        _add(db, **overrides)

    assert storage.objects == {}
    assert db.query(HomeShowcaseImage).count() == 0


def test_delete_removes_row_and_object(db, storage):
    row = _add(db)

    assert asyncio.run(home_showcase_service.delete_showcase_image(db, row.id)) is True
    assert storage.objects == {}
    assert home_showcase_service.list_showcase_images(db) == []
    assert asyncio.run(home_showcase_service.delete_showcase_image(db, row.id)) is False


def _seed_generated(db):
    db.add(User(id="admin-1", username="admin"))
    db.add(User(id="user-2", username="other"))
    db.add(Conversation(id="conv-1", user_id="admin-1", title="生图", model_id="m"))
    db.add(Conversation(id="conv-2", user_id="user-2", title="别人的", model_id="m"))

    def image_block(file_id, prompt, title=None):
        block = {"type": "generated_image", "file_id": file_id, "prompt": prompt, "width": 864, "height": 1312}
        return {**block, "title": title} if title else block

    db.add(Message(id="m1", conversation_id="conv-1", role="assistant", content=[image_block("f-old", "旧图")]))
    db.add(
        Message(
            id="m2",
            conversation_id="conv-1",
            role="assistant",
            content=[
                {"type": "text", "content": "好的"},
                image_block("f-new", "新图", "信息图"),
                image_block("f-old", "旧图"),
            ],
        )
    )
    db.add(Message(id="m3", conversation_id="conv-1", role="user", content=[image_block("f-user", "用户消息")]))
    db.add(Message(id="m4", conversation_id="conv-2", role="assistant", content=[image_block("f-other", "别人的图")]))
    db.commit()
    db.query(Message).filter(Message.id == "m1").update({"created_at": datetime(2026, 10, 1, tzinfo=UTC)})
    db.query(Message).filter(Message.id == "m2").update({"created_at": datetime(2026, 10, 2, tzinfo=UTC)})
    db.commit()


def test_list_generated_images_only_own_assistant_images_newest_first(db):
    _seed_generated(db)

    images = home_showcase_service.list_generated_images(db, "admin-1")

    assert images == [
        {"file_id": "f-new", "prompt": "新图", "title": "信息图", "width": 864, "height": 1312},
        {"file_id": "f-old", "prompt": "旧图", "title": None, "width": 864, "height": 1312},
    ]


class FakeFileService:
    def __init__(self):
        self.files = {("f-new", "admin-1"): _png(864, 1312), ("f-old", "admin-1"): _png(864, 1312)}

    async def get_file_url(self, file_id, user_id, variant):
        return f"/signed/{file_id}/{variant}" if (file_id, user_id) in self.files else None

    async def get_file_content(self, file_id, user_id, variant):
        data = self.files.get((file_id, user_id))
        return (data, "image/png") if data is not None else None


def _client(db, *, admin=True):
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req-1"
        return await call_next(request)

    app.include_router(prompts.router, prefix="/api/prompts")
    app.include_router(prompts.admin_router, prefix="/api/admin/home-showcase")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_file_service] = FakeFileService
    audit_calls = []
    app.dependency_overrides[get_admin_audit_service] = lambda: SimpleNamespace(
        _record=lambda **kwargs: audit_calls.append(kwargs)
    )
    if admin:
        app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id="admin-1")
    return TestClient(app), audit_calls


def test_admin_upload_then_public_list_and_image(db, storage):
    client, audit_calls = _client(db)

    created = client.post(
        "/api/admin/home-showcase",
        files={"file": ("cat.png", _png(600, 600), "image/png")},
        data={"label": "水彩插画", "prompt": "画一只水彩风格的橘猫"},
    )
    assert created.status_code == 200
    image = created.json()["data"]
    assert audit_calls[0]["action"] == "admin.home_showcase.create"

    listed = client.get("/api/prompts/showcase").json()["data"]["images"]
    assert [item["id"] for item in listed] == [image["id"]]

    response = client.get(image["image_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert "immutable" in response.headers["cache-control"]

    assert client.delete(f"/api/admin/home-showcase/{image['id']}").status_code == 200
    assert client.get("/api/prompts/showcase").json()["data"]["images"] == []
    assert client.get(image["image_url"]).status_code == 404


def test_admin_picks_own_generated_image_into_pool(db, storage):
    _seed_generated(db)
    client, audit_calls = _client(db)

    generated = client.get("/api/admin/home-showcase/generated").json()["data"]["images"]
    assert [(item["file_id"], item["thumbnail_url"], item["in_pool"]) for item in generated] == [
        ("f-new", "/signed/f-new/thumbnail", False),
        ("f-old", "/signed/f-old/thumbnail", False),
    ]

    created = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"file_id": "f-new", "label": "信息图", "prompt": "新图"},
    )
    assert created.status_code == 200
    image = created.json()["data"]
    assert (image["source_file_id"], image["width"], image["height"]) == ("f-new", 480, 729)
    assert audit_calls[0]["metadata"] == {"label": "信息图", "source_file_id": "f-new"}

    generated = client.get("/api/admin/home-showcase/generated").json()["data"]["images"]
    assert [item["in_pool"] for item in generated] == [True, False]

    # 别人的图按当前管理员取不到
    other = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"file_id": "f-other", "label": "别人的", "prompt": "别人的图"},
    )
    assert other.status_code == 404
    assert db.query(HomeShowcaseImage).count() == 1


def test_admin_endpoints_require_admin(db, storage):
    client, _ = _client(db, admin=False)

    response = client.post(
        "/api/admin/home-showcase",
        files={"file": ("cat.png", _png(600, 600), "image/png")},
        data={"label": "水彩插画", "prompt": "画一只水彩风格的橘猫"},
    )

    assert response.status_code in {401, 403}
    assert storage.objects == {}
