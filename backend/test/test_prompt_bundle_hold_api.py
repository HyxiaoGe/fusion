"""专用 hold 操作沿用管理员鉴权，操作者由登录身份提供。"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import admin
from app.api.deps import get_current_user
from app.schemas.response import ApiException


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/admin")

    @app.exception_handler(ApiException)
    async def api_error(request: Request, exc: ApiException):
        return JSONResponse({"message": exc.message}, status_code=exc.status_code)

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="trusted-admin", is_superuser=True)
    return TestClient(app)


@pytest.mark.parametrize(
    "method,path",
    [("get", "/prompt-bundle/hold"), ("post", "/prompt-bundle/hold"), ("post", "/prompt-bundle/hold/release")],
)
def test_all_hold_routes_reject_normal_users(client, method, path):
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="normal-user", is_superuser=False)
    response = client.request(method, "/api/admin" + path, json={"target_revision": "a" * 64, "reason": "回滚"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,operation", [("/hold", "enter_prompt_bundle_hold"), ("/hold/release", "release_prompt_bundle_hold")]
)
def test_admin_actor_cannot_be_forged_by_request(client, path, operation):
    with patch.object(admin, operation, return_value={"state": "held"}) as mocked:
        response = client.post(
            "/api/admin/prompt-bundle" + path, json={"target_revision": "a" * 64, "reason": "回归定位"}
        )
    assert response.status_code == 200
    mocked.assert_called_once_with("a" * 64, actor="trusted-admin", reason="回归定位")
    forged = client.post(
        "/api/admin/prompt-bundle" + path, json={"target_revision": "a" * 64, "reason": "回归定位", "actor": "forged"}
    )
    assert forged.status_code == 422


def test_admin_can_read_current_database_state(client):
    with patch.object(admin, "get_prompt_bundle_hold", return_value={"state": "held", "target_revision": "a" * 64}):
        response = client.get("/api/admin/prompt-bundle/hold")
    assert response.status_code == 200
    assert response.json()["data"]["target_revision"] == "a" * 64
