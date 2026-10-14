import os
import unittest
from importlib.metadata import version

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.db.mcp_server_repository import McpServerRepository  # noqa: E402
from app.db.models import McpServer  # noqa: E402


class McpPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        McpServer.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.repository = McpServerRepository(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_repository_persists_reference_and_snapshots_without_secret_value(self):
        row = self.repository.create(
            {
                "name": "百炼搜索",
                "provider": "aliyun",
                "endpoint_url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
                "transport": "streamable_http",
                "auth_type": "bearer",
                "auth_name": None,
                "credential_ref": "DASHSCOPE_API_KEY",
                "is_enabled": False,
                "allowed_tools": [],
                "discovered_tools": [],
                "health_status": "disabled",
            }
        )

        self.assertEqual(self.repository.get(row.id).credential_ref, "DASHSCOPE_API_KEY")
        self.assertFalse(hasattr(row, "credential"))
        self.assertFalse(hasattr(row, "secret"))

        updated = self.repository.update(
            row,
            {
                "discovered_tools": [{"name": "search", "description": "搜索", "input_schema": {"type": "object"}}],
                "allowed_tools": ["search"],
                "health_status": "healthy",
            },
        )
        self.assertEqual(updated.allowed_tools, ["search"])
        self.assertEqual(updated.config_version, 2)
        self.assertEqual(self.repository.list_all()[0].health_status, "healthy")

        stale_update = self.repository.update_if_version(
            row.id,
            expected_version=1,
            values={"health_status": "unhealthy"},
        )
        self.assertIsNone(stale_update)
        self.assertEqual(self.repository.get(row.id).health_status, "healthy")

    def _audit(self, audit_service):
        from types import SimpleNamespace

        admin = SimpleNamespace(id="admin-1", username="admin", email=None)

        def record(server_id):
            audit_service._record(
                admin=admin,
                action="admin.mcp.server.create",
                resource_type="mcp_server",
                resource_id=server_id,
                request_id="req-1",
                metadata={"name": "百炼搜索"},
                commit=False,
            )

        return record

    def _values(self):
        return {
            "name": "百炼搜索",
            "provider": "aliyun",
            "endpoint_url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
            "transport": "streamable_http",
            "auth_type": "bearer",
            "auth_name": None,
            "credential_ref": "DASHSCOPE_API_KEY",
            "is_enabled": False,
            "allowed_tools": [],
            "discovered_tools": [],
            "health_status": "disabled",
        }

    def test_audit_event_commits_with_the_write(self):
        from app.db.admin_audit_repository import AdminAuditRepository
        from app.db.models import AdminAuditEvent
        from app.services.admin_audit_service import AdminAuditService

        AdminAuditEvent.__table__.create(self.engine)
        audit_service = AdminAuditService(AdminAuditRepository(self.db))

        row = self.repository.create(self._values(), before_commit=self._audit(audit_service))

        events = self.db.query(AdminAuditEvent).all()
        self.assertEqual([(event.action, event.resource_id) for event in events], [("admin.mcp.server.create", row.id)])

    def test_audit_failure_rolls_back_the_write(self):
        from app.db.admin_audit_repository import AdminAuditRepository
        from app.schemas.response import ApiException
        from app.services.admin_audit_service import AdminAuditService

        # 审计表不存在时写审计失败，配置写入必须一起回滚。
        audit_service = AdminAuditService(AdminAuditRepository(self.db))

        with self.assertRaises(ApiException):
            self.repository.create(self._values(), before_commit=self._audit(audit_service))

        self.assertEqual(self.repository.list_all(), [])

    def test_runtime_dependency_versions_are_exactly_compatible_pins(self):
        self.assertEqual(version("mcp"), "1.28.1")
        self.assertEqual(version("sse-starlette"), "2.4.1")


if __name__ == "__main__":
    unittest.main()
