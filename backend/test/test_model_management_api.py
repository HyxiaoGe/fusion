import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"
os.environ["SERVER_HOST"] = "http://dev.example:8002"
os.environ["FRONTEND_URL"] = "http://dev.example:3004"
os.environ["AUTH_SERVICE_BASE_URL"] = "http://auth.example:8100"
os.environ["AUTH_SERVICE_CLIENT_ID"] = "fusion-client"
os.environ["AUTH_SERVICE_JWKS_URL"] = "http://auth.example:8100/.well-known/jwks.json"


class FakeModelManagementService:
    def __init__(self):
        self.calls = []

    def get_snapshot(self):
        return {
            "generated_at": "2026-08-04T00:00:00+00:00",
            "models": [
                {
                    "model_id": "qwen/max",
                    "name": "Qwen Max",
                    "provider": "qwen",
                    "provider_display": "通义千问",
                    "health": "healthy",
                    "selectable": False,
                    "routable": True,
                    "state": "hidden",
                    "revision": 1,
                    "reason": "暂时隐藏",
                    "updated_at": None,
                }
            ],
        }

    def set_visibility(self, **kwargs):
        self.calls.append(("visibility", kwargs))
        return SimpleNamespace(
            model_id=kwargs["model_id"],
            selectable=kwargs["selectable"],
            routable=True,
            revision=2,
            reason=kwargs["reason"],
            updated_by=kwargs["admin"].id,
            updated_at=None,
        )


class FakeControlRepository:
    def get_by_model_ids(self, model_ids):
        if "qwen-max-latest" not in model_ids:
            return {}
        return {
            "qwen-max-latest": SimpleNamespace(
                model_id="qwen-max-latest",
                selectable=False,
                routable=True,
                revision=3,
                reason="隐藏",
                updated_by="admin-1",
                updated_at=None,
            )
        }

    def get(self, model_id):
        return self.get_by_model_ids([model_id]).get(model_id)


class ModelManagementApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)

    def setUp(self):
        from app.api.deps import (
            get_current_admin_user,
            get_model_management_service,
        )

        self.service = FakeModelManagementService()
        self.admin = SimpleNamespace(id="admin-1", is_superuser=True)
        self.main.app.dependency_overrides[get_current_admin_user] = lambda: self.admin
        self.main.app.dependency_overrides[get_model_management_service] = lambda: self.service

    def tearDown(self):
        self.main.app.dependency_overrides.clear()

    def test_admin_snapshot_has_exact_contract_and_is_never_cacheable(self):
        response = self.client.get("/api/admin/model-management")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        data = response.json()["data"]
        self.assertEqual(set(data), {"generated_at", "models"})
        self.assertEqual(
            set(data["models"][0]),
            {
                "model_id",
                "name",
                "provider",
                "provider_display",
                "health",
                "selectable",
                "routable",
                "state",
                "revision",
                "reason",
                "updated_at",
            },
        )

    def test_visibility_route_uses_body_model_id_and_revision(self):
        hidden = self.client.patch(
            "/api/admin/model-management/models/visibility",
            json={
                "model_id": "qwen/max",
                "selectable": False,
                "reason": "暂时隐藏",
                "expected_revision": 1,
            },
        )

        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()["data"]["model_id"], "qwen/max")
        self.assertEqual(self.service.calls[0][1]["expected_revision"], 1)

    def test_admission_routes_are_gone(self):
        # 上下线改走 scripts/model_onboard.py，后台不再有候选准入与 Worker 协议。
        admit = self.client.post(f"/api/admin/model-management/candidates/{'a' * 64}/admit", json={})
        claim = self.client.post("/api/internal/model-management/admissions/claim")

        self.assertEqual(admit.status_code, 404)
        self.assertEqual(claim.status_code, 404)

    def test_admin_endpoint_is_superuser_only(self):
        from app.api.deps import get_current_admin_user, get_current_user

        self.main.app.dependency_overrides.pop(get_current_admin_user)
        self.main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1", is_superuser=False)
        forbidden = self.client.get("/api/admin/model-management")
        self.assertEqual(forbidden.status_code, 403)

    def test_public_models_list_and_detail_keep_hidden_model_routable(self):
        from app.api.deps import get_model_catalog_control_repository

        catalog = {
            "qwen-max-latest": {
                "underlying": "openai/qwen-max",
                "metadata": {"provider_key": "qwen", "capabilities": {"functionCalling": True}},
                "max_input_tokens": 32768,
                "max_output_tokens": 8192,
                "db_model": True,
            },
            "deepseek-chat": {
                "underlying": "deepseek/deepseek-chat",
                "metadata": {"provider_key": "deepseek"},
                "db_model": True,
            },
        }
        self.main.app.dependency_overrides[get_model_catalog_control_repository] = lambda: FakeControlRepository()

        with (
            patch("app.api.models.litellm_catalog.list_aliases", return_value=catalog),
            patch("app.api.models.litellm_catalog.get_model_entry", side_effect=catalog.get),
        ):
            listed = self.client.get("/api/models/")
            detailed = self.client.get("/api/models/qwen-max-latest")

        self.assertEqual(listed.status_code, 200)
        models = {item["modelId"]: item for item in listed.json()["data"]["models"]}
        self.assertFalse(models["qwen-max-latest"]["selectable"])
        self.assertTrue(models["qwen-max-latest"]["routable"])
        self.assertTrue(models["qwen-max-latest"]["enabled"])
        self.assertTrue(models["deepseek-chat"]["selectable"])
        self.assertEqual(detailed.status_code, 200)
        self.assertFalse(detailed.json()["data"]["selectable"])
        self.assertTrue(detailed.json()["data"]["routable"])
        self.assertEqual(detailed.headers["cache-control"], "private, no-store")


if __name__ == "__main__":
    unittest.main()
