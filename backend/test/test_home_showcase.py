import asyncio
import io
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
from app.db.models import HomeShowcaseImage
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
    HomeShowcaseImage.__table__.create(engine)
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


def test_admin_endpoints_require_admin(db, storage):
    client, _ = _client(db, admin=False)

    response = client.post(
        "/api/admin/home-showcase",
        files={"file": ("cat.png", _png(600, 600), "image/png")},
        data={"label": "水彩插画", "prompt": "画一只水彩风格的橘猫"},
    )

    assert response.status_code in {401, 403}
    assert storage.objects == {}
