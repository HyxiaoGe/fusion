import importlib
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"
os.environ["SERVER_HOST"] = "http://dev.example:8002"
os.environ["FRONTEND_URL"] = "http://dev.example:3004"
os.environ["AUTH_SERVICE_BASE_URL"] = "http://auth.example:8100"
os.environ["AUTH_SERVICE_CLIENT_ID"] = "fusion-client"
os.environ["AUTH_SERVICE_JWKS_URL"] = "http://auth.example:8100/.well-known/jwks.json"


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def build_row(**overrides):
    values = {
        "id": "server-1",
        "name": "百炼搜索",
        "provider": "aliyun",
        "endpoint_url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
        "transport": "streamable_http",
        "auth_type": "bearer",
        "auth_name": None,
        "credential_ref": "DASHSCOPE_API_KEY",
        "is_enabled": False,
        "allowed_tools": [],
        "discovered_tools": [{"name": "search", "description": "搜索", "input_schema": {"type": "object"}}],
        "health_status": "disabled",
        "last_checked_at": None,
        "last_error_code": None,
        "last_error_message": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeService:
    def __init__(self):
        self.row = build_row()
        self.calls = []

    def list_servers(self):
        self.calls.append(("list",))
        return [self.row]

    def create_server(self, request):
        self.calls.append(("create", request))
        return self.row

    def update_server(self, server_id, request):
        self.calls.append(("update", server_id, request))
        return self.row

    def set_status(self, server_id, request):
        self.calls.append(("status", server_id, request))
        self.row.is_enabled = request.is_enabled
        self.row.health_status = "unknown" if request.is_enabled else "disabled"
        return self.row

    async def test_server(self, server_id):
        self.calls.append(("test", server_id))
        self.row.health_status = "unhealthy"
        self.row.last_error_code = "auth_failed"
        self.row.last_error_message = "MCP 服务鉴权失败"
        self.row.last_checked_at = NOW
        return self.row

    async def refresh_tools(self, server_id):
        self.calls.append(("refresh", server_id))
        self.row.health_status = "healthy"
        self.row.last_error_code = None
        self.row.last_error_message = None
        self.row.last_checked_at = NOW
        return self.row


class AdminMcpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)

    def setUp(self):
        from app.api.deps import get_current_admin_user, get_mcp_server_service

        self.service = FakeService()
        self.main.app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(
            id="admin-1", is_superuser=True
        )
        self.main.app.dependency_overrides[get_mcp_server_service] = lambda: self.service

    def tearDown(self):
        self.main.app.dependency_overrides.clear()

    def test_get_servers_returns_direct_array_with_fixed_contract(self):
        response = self.client.get("/api/admin/mcp/servers")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["id"], "server-1")
        self.assertEqual(
            data[0]["discovered_tools"],
            [{"name": "search", "description": "搜索", "input_schema": {"type": "object"}}],
        )
        self.assertEqual(data[0]["credential_ref"], "DASHSCOPE_API_KEY")
        self.assertNotIn("secret", response.text.lower())
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_mcp_admin_api_rejects_non_admin_user(self):
        from app.api.deps import get_current_admin_user, get_current_user

        self.main.app.dependency_overrides.pop(get_current_admin_user)
        self.main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1", is_superuser=False)

        response = self.client.get("/api/admin/mcp/servers")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["message"], "需要管理员权限")

    def test_create_and_patch_accept_cross_repo_payload(self):
        payload = {
            "name": "百炼搜索",
            "provider": "aliyun",
            "endpoint_url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
            "transport": "streamable_http",
            "auth_type": "bearer",
            "credential_ref": "DASHSCOPE_API_KEY",
            "allowed_tools": [],
        }

        created = self.client.post("/api/admin/mcp/servers", json=payload)
        patched = self.client.patch("/api/admin/mcp/servers/server-1", json={**payload, "name": "百炼搜索 2"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(self.service.calls[0][0], "create")
        self.assertEqual(self.service.calls[1][0], "update")

    def test_status_test_and_refresh_return_full_server_even_when_test_is_unhealthy(self):
        enabled = self.client.post("/api/admin/mcp/servers/server-1/status", json={"is_enabled": True})
        tested = self.client.post("/api/admin/mcp/servers/server-1/test")
        refreshed = self.client.post("/api/admin/mcp/servers/server-1/tools/refresh")

        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()["data"]["is_enabled"])
        self.assertEqual(tested.status_code, 200)
        self.assertEqual(tested.json()["data"]["health_status"], "unhealthy")
        self.assertEqual(tested.json()["data"]["last_error_code"], "auth_failed")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["data"]["health_status"], "healthy")


if __name__ == "__main__":
    unittest.main()


class McpProbeFreshnessAndCircuitScopeTests(unittest.TestCase):
    """issue #32 缺陷 2：health_status 只反映主动探测，不能被当成实时健康。

    保留 health_status 的原语义（上次探测结果），另外把探测新鲜度与进程级熔断状态
    并列暴露出来，让管理视图不会再把过期的 healthy 当作"现在是好的"。
    """

    def test_从未探测过标记为_never(self):
        from app.api.admin_mcp import resolve_probe_freshness

        assert resolve_probe_freshness(None, now=datetime(2026, 9, 5, tzinfo=UTC)) == "never"

    def test_有效期内为_fresh(self):
        from app.api.admin_mcp import resolve_probe_freshness

        now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        assert resolve_probe_freshness(now - timedelta(minutes=5), now=now) == "fresh"

    def test_超过有效期为_stale(self):
        from app.api.admin_mcp import resolve_probe_freshness

        now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        assert resolve_probe_freshness(now - timedelta(days=3), now=now) == "stale"

    def test_无时区的时间戳按_UTC_解释而不是崩掉(self):
        from app.api.admin_mcp import resolve_probe_freshness

        now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        assert resolve_probe_freshness(datetime(2026, 9, 5, 11, 55), now=now) == "fresh"

    def test_熔断状态快照按服务隔离(self):
        from app.services.mcp.agent_tools import McpAgentServerCircuitBreaker, _McpCircuitState

        breaker = McpAgentServerCircuitBreaker(failure_threshold=2, cooldown_seconds=30, clock=lambda: 1000.0)
        assert breaker.snapshot("server-a") == "closed"

        breaker._states["server-a"] = _McpCircuitState(consecutive_failures=2, opened_at=990.0)
        assert breaker.snapshot("server-a") == "open"
        assert breaker.snapshot("server-b") == "closed"

    def test_冷却期过后快照为_half_open(self):
        from app.services.mcp.agent_tools import McpAgentServerCircuitBreaker, _McpCircuitState

        breaker = McpAgentServerCircuitBreaker(failure_threshold=2, cooldown_seconds=30, clock=lambda: 1000.0)
        breaker._states["server-a"] = _McpCircuitState(consecutive_failures=2, opened_at=900.0)

        assert breaker.snapshot("server-a") == "half_open"

    def test_熔断作用域被显式标注为进程级(self):
        from app.api.admin_mcp import RUNTIME_CIRCUIT_SCOPE

        # 熔断器是进程内内存态，多 worker 下各自独立；管理视图不得把它当全局实时健康。
        assert RUNTIME_CIRCUIT_SCOPE == "process"
