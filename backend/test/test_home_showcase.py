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
from app.db.models import Conversation, HomeShowcaseImage, Message, ToolCallLog, User
from app.services import home_showcase_service


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, key, data, content_type):
        self.objects[key] = data
        return key

    async def download(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
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
    for model in (User, Conversation, Message, ToolCallLog, HomeShowcaseImage):
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


def test_add_stores_480_short_side_card_and_1600_long_side_full_webp(db, storage):
    row = _add(db, content=_png(2000, 3000), object_position="top")

    assert (row.width, row.height) == (480, 720)
    stored = Image.open(io.BytesIO(storage.objects[row.storage_key]))
    assert stored.format == "WEBP" and stored.size == (480, 720)
    full = Image.open(io.BytesIO(storage.objects[home_showcase_service.full_storage_key(row.id)]))
    assert full.format == "WEBP" and full.size == (1067, 1600)
    assert home_showcase_service.serialize_showcase_image(row) == {
        "id": row.id,
        "label": "水彩插画",
        "prompt": "画一只水彩风格的橘猫",
        "object_position": "top",
        "source_file_id": None,
        "image_url": f"/api/prompts/showcase/{row.id}/image",
        "full_image_url": f"/api/prompts/showcase/{row.id}/image/full",
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
        block = {
            "type": "generated_image",
            "file_id": file_id,
            "prompt": prompt,
            "width": 864,
            "height": 1312,
            "tool_call_log_id": f"log-{file_id}",
        }
        return {**block, "title": title} if title else block

    def generated(
        message_id, day, conversation_id, file_id, prompt, params, *, user_id="admin-1", title=None, extra=()
    ):
        db.add(
            ToolCallLog(
                id=f"log-{file_id}",
                conversation_id=conversation_id,
                user_id=user_id,
                tool_name="generate_image",
                status="success",
                model_id="m",
                provider="image-service",
                input_params={"prompt": prompt, **params},
            )
        )
        db.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=[{"type": "text", "content": "好的"}, image_block(file_id, prompt, title), *extra],
                created_at=datetime(2026, 10, day, tzinfo=UTC),
            )
        )

    # f-a 编辑出 f-b，f-b 再编辑出 f-e；f-c 之后在同会话里重画出 f-d
    generated("m1", 1, "conv-1", "f-a", "初版", {})
    generated("m2", 2, "conv-1", "f-b", "改一版", {"source_image_id": "f-a"}, title="信息图")
    generated("m3", 3, "conv-1", "f-c", "另一张", {})
    generated("m4", 4, "conv-1", "f-d", "另一张", {"regenerate": True})
    # 回复里重复出现的旧图块只算一次
    generated("m5", 5, "conv-1", "f-e", "再改", {"source_image_id": "f-b"}, extra=[image_block("f-a", "初版")])
    generated("m6", 6, "conv-2", "f-other", "别人的图", {}, user_id="user-2")
    db.add(Message(id="m7", conversation_id="conv-1", role="user", content=[image_block("f-user", "用户消息")]))
    db.commit()


def _file_ids(groups):
    return [[image["file_id"] for image in group["versions"]] for group in groups]


def test_generated_groups_follow_edit_and_regenerate_records_newest_first(db):
    _seed_generated(db)

    groups, next_offset = home_showcase_service.list_generated_groups(db, "admin-1")

    assert next_offset is None
    assert _file_ids(groups) == [["f-e", "f-b", "f-a"], ["f-d", "f-c"]]
    # key 是链上最早那张图，排除部分版本后不变
    assert [group["key"] for group in groups] == ["f-a", "f-c"]
    assert groups[0]["versions"][1] == {
        "file_id": "f-b",
        "prompt": "改一版",
        "title": "信息图",
        "width": 864,
        "height": 1312,
    }


def test_generated_groups_exclude_versions_and_page_by_group(db):
    _seed_generated(db)

    excluded, _ = home_showcase_service.list_generated_groups(
        db, "admin-1", exclude_file_ids=frozenset({"f-e", "f-c", "f-d"})
    )
    first, next_offset = home_showcase_service.list_generated_groups(db, "admin-1", page_size=1)
    second, last_offset = home_showcase_service.list_generated_groups(db, "admin-1", offset=next_offset, page_size=1)

    # 整组都被排除时这组不出现
    assert _file_ids(excluded) == [["f-b", "f-a"]]
    assert excluded[0]["key"] == "f-a"
    assert (_file_ids(first), next_offset) == ([["f-e", "f-b", "f-a"]], 1)
    assert (_file_ids(second), last_offset) == ([["f-d", "f-c"]], None)


class FakeFileService:
    def __init__(self):
        self.files = {(file_id, "admin-1"): _png(864, 1312) for file_id in ("f-a", "f-b", "f-c", "f-d", "f-e")}

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
    full = client.get(image["full_image_url"])
    assert full.status_code == 200
    assert Image.open(io.BytesIO(full.content)).size == (600, 600)

    assert client.delete(f"/api/admin/home-showcase/{image['id']}").status_code == 200
    assert client.get("/api/prompts/showcase").json()["data"]["images"] == []
    assert client.get(image["image_url"]).status_code == 404
    assert client.get(image["full_image_url"]).status_code == 404
    assert storage.objects == {}


def test_admin_picks_own_generated_image_into_pool(db, storage):
    _seed_generated(db)
    client, audit_calls = _client(db)

    def listed(**params):
        data = client.get("/api/admin/home-showcase/generated", params=params).json()["data"]
        return data["next_offset"], [
            [(item["file_id"], item["thumbnail_url"], item["in_pool"]) for item in group["versions"]]
            for group in data["groups"]
        ]

    assert listed() == (
        None,
        [
            [
                ("f-e", "/signed/f-e/thumbnail", False),
                ("f-b", "/signed/f-b/thumbnail", False),
                ("f-a", "/signed/f-a/thumbnail", False),
            ],
            [("f-d", "/signed/f-d/thumbnail", False), ("f-c", "/signed/f-c/thumbnail", False)],
        ],
    )

    created = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"file_id": "f-b", "label": "信息图", "prompt": "改一版"},
    )
    assert created.status_code == 200
    image = created.json()["data"]
    assert (image["source_file_id"], image["width"], image["height"]) == ("f-b", 480, 729)
    assert audit_calls[0]["metadata"] == {"label": "信息图", "source_file_id": "f-b"}

    _, groups = listed()
    assert [item[2] for item in groups[0]] == [False, True, False]
    hidden = client.get("/api/admin/home-showcase/generated", params={"hide_in_pool": "true"}).json()["data"]
    assert [(group["key"], [item["file_id"] for item in group["versions"]]) for group in hidden["groups"]] == [
        ("f-a", ["f-e", "f-a"]),
        ("f-c", ["f-d", "f-c"]),
    ]

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
