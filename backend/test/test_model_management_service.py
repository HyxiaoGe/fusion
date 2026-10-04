import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.admin_audit_repository import AdminAuditRepository
from app.db.database import Base
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.db.models import AdminAuditEvent, ModelCatalogControl
from app.schemas.response import ApiException
from app.services.admin_audit_service import AdminAuditService
from app.services.model_management_service import ModelManagementConfig, ModelManagementService


class FakeCatalog:
    def __init__(self):
        self.entries = {
            "qwen-max-latest": {
                "db_model": True,
                "underlying": "openai/qwen-max",
                "metadata": {"display_name": "Qwen Max", "provider_key": "qwen"},
            }
        }

    def list_aliases(self):
        return self.entries

    def get_model_entry(self, model_id):
        return self.entries.get(model_id)


class ModelManagementServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.control_repository = ModelCatalogControlRepository(self.db)
        self.audit = AdminAuditService(AdminAuditRepository(self.db))
        self.catalog = FakeCatalog()
        self.admin = SimpleNamespace(
            id="admin-1",
            username="root",
            email="root@example.com",
            is_superuser=True,
        )
        self.now = datetime(2026, 8, 4, 1, 2, 3, 456789, tzinfo=UTC)

    def tearDown(self):
        self.db.close()

    def build_service(self, **overrides):
        config_values = {"management_enabled": True}
        config_values.update(overrides.pop("config", {}))
        return ModelManagementService(
            control_repository=self.control_repository,
            audit_service=overrides.pop("audit_service", self.audit),
            config=ModelManagementConfig(**config_values),
            catalog=overrides.pop("catalog", self.catalog),
            clock=overrides.pop("clock", lambda: self.now),
        )

    def test_snapshot_lists_registered_models_with_visibility(self):
        service = self.build_service()
        with patch("app.services.model_management_service.litellm_health.get_health", return_value=None):
            snapshot = service.get_snapshot()

        self.assertEqual(set(snapshot), {"generated_at", "models"})
        self.assertEqual(snapshot["generated_at"], "2026-08-04T01:02:03.456789+00:00")
        model = snapshot["models"][0]
        self.assertEqual(
            {key: model[key] for key in ("model_id", "name", "provider", "selectable", "routable", "state")},
            {
                "model_id": "qwen-max-latest",
                "name": "Qwen Max",
                "provider": "qwen",
                "selectable": True,
                "routable": True,
                "state": "selectable",
            },
        )

    def test_visibility_uses_cas_and_rolls_back_with_audit(self):
        service = self.build_service()
        hidden = service.set_visibility(
            model_id="qwen-max-latest",
            selectable=False,
            expected_revision=None,
            reason="暂时隐藏",
            admin=self.admin,
            request_id="req-1",
        )
        self.assertEqual((hidden.selectable, hidden.routable, hidden.revision), (False, True, 1))

        with self.assertRaises(ApiException) as stale:
            service.set_visibility(
                model_id="qwen-max-latest",
                selectable=True,
                expected_revision=None,
                reason="恢复展示",
                admin=self.admin,
                request_id="req-2",
            )
        self.assertEqual(stale.exception.status_code, 409)

        failing_audit = Mock()
        failing_audit._record.side_effect = RuntimeError("audit unavailable")
        service = self.build_service(audit_service=failing_audit)
        with self.assertRaises(RuntimeError):
            service.set_visibility(
                model_id="qwen-max-latest",
                selectable=True,
                expected_revision=1,
                reason="恢复展示",
                admin=self.admin,
                request_id="req-3",
            )
        self.db.expire_all()
        self.assertFalse(self.db.query(ModelCatalogControl).one().selectable)
        self.assertEqual(self.db.query(AdminAuditEvent).count(), 1)

    def test_visibility_write_is_rejected_when_management_is_read_only(self):
        service = self.build_service(config={"management_enabled": False})

        with self.assertRaises(ApiException) as raised:
            service.set_visibility(
                model_id="qwen-max-latest",
                selectable=False,
                expected_revision=None,
                reason="尝试隐藏",
                admin=self.admin,
                request_id="req-read-only",
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.db.query(ModelCatalogControl).count(), 0)
        self.assertEqual(self.db.query(AdminAuditEvent).count(), 0)


if __name__ == "__main__":
    unittest.main()
