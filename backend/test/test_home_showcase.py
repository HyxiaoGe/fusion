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
from app.api.deps import get_admin_audit_service, get_current_admin_user
from app.db.database import get_db
from app.db.models import Conversation, GeneratedImage, HomeShowcaseImage, Message, ToolCallLog, User
from app.services import home_showcase_service


def iid(name: str) -> str:
    """image-service 的图片编号（uuid4 形状），按名字固定生成。"""
    return f"00000000-0000-4000-8000-{name.encode().hex():0>12}"[:36]


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
    for model in (User, Conversation, Message, ToolCallLog, HomeShowcaseImage, GeneratedImage):
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
        "source_image_id": None,
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

    def image_block(name, prompt):
        return {
            "type": "generated_image",
            "schema_version": 2,
            "image_id": iid(name),
            "prompt": prompt,
            "width": 864,
            "height": 1312,
            "tool_call_log_id": f"log-{name}",
        }

    def generated(message_id, day, conversation_id, name, prompt, params, *, user_id="admin-1", extra=()):
        db.add(
            ToolCallLog(
                id=f"log-{name}",
                conversation_id=conversation_id,
                user_id=user_id,
                tool_name="generate_image",
                status="success",
                model_id="m",
                provider="image",
                input_params={"prompt": prompt, **params},
            )
        )
        db.add(GeneratedImage(id=iid(name), conversation_id=conversation_id, user_id=user_id, mime_type="image/png"))
        db.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=[{"type": "text", "content": "好的"}, image_block(name, prompt), *extra],
                created_at=datetime(2026, 10, day, tzinfo=UTC),
            )
        )

    # a 编辑出 b，b 再编辑出 e；c 之后在同会话里重画出 d
    generated("m1", 1, "conv-1", "a", "初版", {})
    generated("m2", 2, "conv-1", "b", "改一版", {"source_image_id": iid("a")})
    generated("m3", 3, "conv-1", "c", "另一张", {})
    generated("m4", 4, "conv-1", "d", "另一张", {"regenerate": True})
    # 回复里重复出现的旧图块只算一次
    generated("m5", 5, "conv-1", "e", "再改", {"source_image_id": iid("b")}, extra=[image_block("a", "初版")])
    generated("m6", 6, "conv-2", "other", "别人的图", {}, user_id="user-2")
    db.add(Message(id="m7", conversation_id="conv-1", role="user", content=[image_block("user", "用户消息")]))
    db.commit()


def _image_ids(groups):
    return [[image["image_id"] for image in group["versions"]] for group in groups]


def _ids(*names):
    return [iid(name) for name in names]


def test_generated_groups_follow_edit_and_regenerate_records_newest_first(db):
    _seed_generated(db)

    groups, next_offset = home_showcase_service.list_generated_groups(db, "admin-1")

    assert next_offset is None
    assert _image_ids(groups) == [_ids("e", "b", "a"), _ids("d", "c")]
    # key 是链上最早那张图，排除部分版本后不变
    assert [group["key"] for group in groups] == _ids("a", "c")
    assert groups[0]["versions"][1] == {
        "image_id": iid("b"),
        "prompt": "改一版",
        "width": 864,
        "height": 1312,
    }


def test_generated_groups_exclude_versions_and_page_by_group(db):
    _seed_generated(db)

    excluded, _ = home_showcase_service.list_generated_groups(
        db, "admin-1", exclude_image_ids=frozenset(_ids("e", "c", "d"))
    )
    first, next_offset = home_showcase_service.list_generated_groups(db, "admin-1", page_size=1)
    second, last_offset = home_showcase_service.list_generated_groups(db, "admin-1", offset=next_offset, page_size=1)

    # 整组都被排除时这组不出现
    assert _image_ids(excluded) == [_ids("b", "a")]
    assert excluded[0]["key"] == iid("a")
    assert (_image_ids(first), next_offset) == ([_ids("e", "b", "a")], 1)
    assert (_image_ids(second), last_offset) == ([_ids("d", "c")], None)


@pytest.fixture(autouse=True)
def image_store(monkeypatch):
    """image-service 里存着的图；按编号取。"""
    images = {image_id: _png(864, 1312) for image_id in _ids("a", "b", "c", "d", "e", "other")}

    async def fetch_image(image_id):
        data = images.get(image_id)
        return (data, "image/png") if data is not None else None

    monkeypatch.setattr(prompts, "fetch_image", fetch_image)
    return images


def _client(db, *, admin=True):
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req-1"
        return await call_next(request)

    app.include_router(prompts.router, prefix="/api/prompts")
    app.include_router(prompts.admin_router, prefix="/api/admin/home-showcase")
    app.dependency_overrides[get_db] = lambda: db
    audit_calls = []
    app.dependency_overrides[get_admin_audit_service] = lambda: SimpleNamespace(
        _record=lambda **kwargs: audit_calls.append(kwargs)
    )
    if admin:
        app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id="admin-1")
    return TestClient(app), audit_calls


def test_admin_pool_image_then_public_list_images_and_unpublish(db, storage):
    _seed_generated(db)
    client, audit_calls = _client(db)

    created = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"image_id": iid("a"), "label": "水彩插画", "prompt": "初版"},
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
    assert Image.open(io.BytesIO(full.content)).size == (864, 1312)

    # 下架即移出图片池，原图回到挑选列表
    assert client.delete(f"/api/admin/home-showcase/{image['id']}").status_code == 200
    assert client.get("/api/prompts/showcase").json()["data"]["images"] == []
    assert client.get(image["image_url"]).status_code == 404
    assert client.get(image["full_image_url"]).status_code == 404
    assert storage.objects == {}
    hidden = client.get("/api/admin/home-showcase/generated", params={"hide_in_pool": "true"}).json()["data"]
    assert [item["image_id"] for item in hidden["groups"][0]["versions"]] == _ids("e", "b", "a")


def test_manual_upload_endpoint_is_gone(db, storage):
    client, _ = _client(db)

    response = client.post(
        "/api/admin/home-showcase",
        files={"file": ("cat.png", _png(600, 600), "image/png")},
        data={"label": "水彩插画", "prompt": "画一只水彩风格的橘猫"},
    )

    assert response.status_code == 405
    assert storage.objects == {}


def test_admin_picks_own_generated_image_into_pool(db, storage):
    _seed_generated(db)
    client, audit_calls = _client(db)

    def listed(**params):
        data = client.get("/api/admin/home-showcase/generated", params=params).json()["data"]
        return (
            data["next_offset"],
            [[(item["image_id"], item["in_pool"]) for item in group["versions"]] for group in data["groups"]],
            [item["thumbnail_url"] for group in data["groups"] for item in group["versions"]],
        )

    next_offset, groups, thumbnails = listed()
    assert (next_offset, groups) == (
        None,
        [
            [(iid("e"), False), (iid("b"), False), (iid("a"), False)],
            [(iid("d"), False), (iid("c"), False)],
        ],
    )
    # 缩略图走签名的生成图地址
    assert thumbnails[0].startswith(f"/api/generated-images/{iid('e')}/content?token=")

    created = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"image_id": iid("b"), "label": "信息图", "prompt": "改一版"},
    )
    assert created.status_code == 200
    image = created.json()["data"]
    assert (image["source_image_id"], image["width"], image["height"]) == (iid("b"), 480, 729)
    assert audit_calls[0]["metadata"] == {"label": "信息图", "source_image_id": iid("b")}

    _, groups, _ = listed()
    assert [item[1] for item in groups[0]] == [False, True, False]
    hidden = client.get("/api/admin/home-showcase/generated", params={"hide_in_pool": "true"}).json()["data"]
    assert [(group["key"], [item["image_id"] for item in group["versions"]]) for group in hidden["groups"]] == [
        (iid("a"), _ids("e", "a")),
        (iid("c"), _ids("d", "c")),
    ]

    # 别人的图按当前管理员取不到
    other = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"image_id": iid("other"), "label": "别人的", "prompt": "别人的图"},
    )
    assert other.status_code == 404
    assert db.query(HomeShowcaseImage).count() == 1


def test_images_whose_conversation_is_gone_are_not_listed_or_pickable(db, storage):
    _seed_generated(db)
    db.query(GeneratedImage).filter(GeneratedImage.id.in_(_ids("c", "d"))).delete(synchronize_session=False)
    db.commit()
    client, _ = _client(db)

    data = client.get("/api/admin/home-showcase/generated").json()["data"]

    assert [[item["image_id"] for item in group["versions"]] for group in data["groups"]] == [_ids("e", "b", "a")]
    response = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"image_id": iid("c"), "label": "没了", "prompt": "另一张"},
    )
    assert response.status_code == 404


def test_admin_endpoints_require_admin(db, storage):
    _seed_generated(db)
    client, _ = _client(db, admin=False)

    response = client.post(
        "/api/admin/home-showcase/from-generated",
        json={"image_id": iid("a"), "label": "水彩插画", "prompt": "初版"},
    )

    assert response.status_code in {401, 403}
    assert storage.objects == {}
