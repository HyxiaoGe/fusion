import unittest
from pathlib import Path

if __package__:
    from .workflow_script_test_support import read_expanded_workflow
else:
    from workflow_script_test_support import read_expanded_workflow


class DeployAuthConfigTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        monorepo_root = root.parent
        self.app_config = (root / "app" / "core" / "config.py").read_text(encoding="utf-8")
        self.env_example = (root / ".env.example").read_text(encoding="utf-8")
        self.workflow = read_expanded_workflow(monorepo_root / ".github" / "workflows" / "_deploy-api.yml")
        self.ci_build_script = (root / ".github" / "scripts" / "linux-build-and-test.sh").read_text(encoding="utf-8")
        self.compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        self.ci_requirements = (root / "requirements-ci.txt").read_text(encoding="utf-8")

    def test_deploy_dev_overrides_auth_internal_base_to_docker_dns(self):
        self.assertIn(
            'export AUTH_SERVICE_INTERNAL_BASE_URL="http://auth-service:8100"',
            self.workflow,
        )

    def test_deploy_health_checks_auth_jwks_from_running_container(self):
        self.assertIn("RESOLVED_AUTH_SERVICE_JWKS_URL", self.workflow)
        self.assertIn("auth JWKS ok", self.workflow)

    def test_auth_clock_skew_leeway_is_documented_and_deployed(self):
        self.assertIn("AUTH_SERVICE_JWT_LEEWAY_SECONDS=5", self.env_example)
        self.assertIn("AUTH_SERVICE_JWT_LEEWAY_SECONDS:-5", self.workflow)

    def test_deploy_runs_api_surface_smoke_after_health(self):
        self.assertIn("Run deployment smoke", self.workflow)
        self.assertIn(
            'python3 "${GITHUB_WORKSPACE}/backend/scripts/deployment_smoke.py" --base-url http://127.0.0.1:8002',
            self.workflow,
        )
        self.assertIn(
            'git -C "${GITHUB_WORKSPACE}" show "${rollback_api_sha}:${rollback_smoke_path}"',
            self.workflow,
        )
        self.assertIn("backend/scripts/deployment_smoke.py", self.workflow)
        self.assertIn("scripts/deployment_smoke.py", self.workflow)
        self.assertIn("| python3 - --base-url http://127.0.0.1:8002", self.workflow)
        self.assertIn("/api/models/", self.workflow)

    def test_deploy_mounts_persistent_file_storage(self):
        self.assertIn('install -d -m 0755 "${FUSION_STORAGE_DIR}"', self.workflow)
        self.assertIn("${FUSION_STORAGE_DIR}:/app/storage/files", self.workflow)
        self.assertIn("FILE_STORAGE_PATH=/app/storage/files", self.workflow)

    def test_deploy_preserves_container_files_before_recreate(self):
        self.assertIn("tar -C /app/storage/files -cf - .", self.workflow)
        self.assertIn('tar -C "${FUSION_STORAGE_DIR}" -xf -', self.workflow)
        self.assertNotIn("docker cp fusion-api:/app/storage/files/.", self.workflow)

    def test_deploy_verifies_persistent_file_storage_after_restart(self):
        self.assertIn("file storage backend ok", self.workflow)
        self.assertIn('settings.FILE_STORAGE_PATH != "/app/storage/files"', self.workflow)
        self.assertIn("OSS_ACCESS_KEY_SECRET", self.workflow)
        self.assertIn("DIRECT_UPLOAD_STALE_SECONDS=${DIRECT_UPLOAD_STALE_SECONDS:-1800}", self.workflow)
        self.assertIn("Access-Control-Request-Method", self.workflow)
        self.assertIn("oss cors PUT method is not allowed", self.workflow)

    def test_ci_runs_tests_with_process_timeout_and_verbose_output(self):
        self.assertIn(
            "timeout 420s python -u -m pytest -v -p no:cacheprovider test",
            self.ci_build_script,
        )

    def test_ci_lint_install_has_process_and_network_timeouts(self):
        self.assertIn("ruff==", self.ci_requirements)
        self.assertIn(
            "timeout 300s python -m pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt",
            self.ci_build_script,
        )

    def test_windows_acr_login_uses_docker_login_action(self):
        self.assertIn(
            "uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0",
            self.workflow,
        )
        self.assertIn("registry: ${{ env.REGISTRY }}", self.workflow)
        self.assertIn("username: ${{ secrets.ACR_USERNAME }}", self.workflow)
        self.assertIn("password: ${{ secrets.ACR_PASSWORD }}", self.workflow)

    def test_linux_acr_login_uses_password_stdin_without_shell_echo(self):
        self.assertIn(
            """printf '%s' "${ACR_PASSWORD}" | docker login""",
            self.workflow,
        )

    def test_deploy_passes_mcp_policy_and_configured_credentials_without_erasing_server_env(self):
        # 高德密钥由 agent-tools 的 amap 代理持有，Fusion 的部署链路不再接触它。
        for text in (self.workflow, self.compose, self.env_example, self.app_config):
            self.assertNotIn("AMAP_MCP_API_KEY", text)
            self.assertNotIn("mcp.amap.com", text)
        # Context7 同样由 agent-tools 的代理持有密钥，Fusion 的部署链路与配置不再出现它。
        for text in (self.workflow, self.compose, self.env_example, self.app_config):
            self.assertNotIn("CONTEXT7_API_KEY", text)
            self.assertNotIn("mcp.context7.com", text)
        # 腾讯地图同样由 agent-tools 的 tmap 代理持有密钥；DashScope 与 Microsoft Learn 的 MCP 已下线。
        for text in (self.workflow, self.compose, self.env_example, self.app_config):
            self.assertNotIn("TENCENT_MAP_MCP_API_KEY", text)
            self.assertNotIn("mcp.map.qq.com", text)
            self.assertNotIn("learn.microsoft.com", text)
            self.assertNotIn("dashscope.aliyuncs.com", text)
        for text in (self.workflow, self.env_example):
            self.assertNotIn("DASHSCOPE_API_KEY", text)
        self.assertIn("append_csv_value()", self.workflow)
        self.assertIn(
            'export MCP_ALLOWED_CREDENTIAL_REFS="$(append_csv_value "${MCP_ALLOWED_CREDENTIAL_REFS}" "AGENT_TOOLS_API_KEY")"',
            self.workflow,
        )
        self.assertIn(
            'export MCP_ALLOWED_HOSTS="${DEPLOY_MCP_ALLOWED_HOSTS:-${MCP_ALLOWED_HOSTS:-}}"',
            self.workflow,
        )
        for variable in (
            "MCP_ALLOWED_HOSTS",
            "MCP_ALLOWED_INTERNAL_ENDPOINTS",
            "MCP_ALLOWED_CREDENTIAL_REFS",
            "MCP_CONNECT_TIMEOUT_SECONDS",
            "MCP_CALL_TIMEOUT_SECONDS",
            "MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS",
            "MCP_ADMIN_OPERATION_TIMEOUT_SECONDS",
            "MCP_MAX_DISCOVERY_PAGES",
            "MCP_MAX_DISCOVERED_TOOLS",
            "MCP_MAX_TOOL_DESCRIPTION_CHARS",
            "MCP_MAX_TOOL_SCHEMA_BYTES",
            "MCP_MAX_RESPONSE_BYTES",
            "MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN",
            "MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD",
            "MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS",
            "AGENT_TOOLS_API_KEY",
        ):
            self.assertIn(f"- {variable}=${{{variable}", self.workflow)
        self.assertIn("DEPLOY_AGENT_TOOLS_API_KEY: ${{ secrets.AGENT_TOOLS_API_KEY }}", self.workflow)
        self.assertIn(
            'export AGENT_TOOLS_API_KEY="${DEPLOY_AGENT_TOOLS_API_KEY:-${AGENT_TOOLS_API_KEY:-}}"',
            self.workflow,
        )
        self.assertIn(
            'export MCP_ALLOWED_CREDENTIAL_REFS="$(append_csv_value "${MCP_ALLOWED_CREDENTIAL_REFS}" "AGENT_TOOLS_API_KEY")"',
            self.workflow,
        )
        # 和风凭证由 agent-tools 的 weather 代理持有，Fusion 的部署链路与配置不再出现它。
        for text in (self.workflow, self.compose, self.env_example, self.app_config):
            self.assertNotIn("QWEATHER_PRIVATE_KEY", text)
            self.assertNotIn("QWEATHER_KEY_ID", text)
            self.assertNotIn("qweatherapi.com", text)
        self.assertIn(
            'export MCP_ALLOWED_INTERNAL_ENDPOINTS="${DEPLOY_MCP_ALLOWED_INTERNAL_ENDPOINTS:-${MCP_ALLOWED_INTERNAL_ENDPOINTS:-}}"',
            self.workflow,
        )
        self.assertIn("agent-tools MCP policy configuration ok", self.workflow)
        self.assertIn(
            '"AGENT_TOOLS_API_KEY" not in settings.RESOLVED_MCP_ALLOWED_CREDENTIAL_REFS',
            self.workflow,
        )

    def test_mcp_timeout_defaults_keep_client_budget_inside_admin_request_budget(self):
        self.assertIn("MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS=12", self.env_example)
        self.assertIn("MCP_ADMIN_OPERATION_TIMEOUT_SECONDS=35", self.env_example)
        self.assertIn("MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS:-12", self.workflow)
        self.assertIn("MCP_ADMIN_OPERATION_TIMEOUT_SECONDS:-35", self.workflow)

    def test_mcp_per_server_run_budget_is_configured_and_deployed(self):
        self.assertIn("MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN: int = int(os.getenv(", self.app_config)
        self.assertIn("MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN=64", self.env_example)
        self.assertIn("MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN:-64", self.workflow)

    def test_mcp_server_circuit_breaker_is_configured_and_deployed(self):
        self.assertIn("MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv(", self.app_config)
        self.assertIn("MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS: float = float(os.getenv(", self.app_config)
        self.assertIn("MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD=3", self.env_example)
        self.assertIn("MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS=30", self.env_example)
        self.assertIn("MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD:-3", self.workflow)
        self.assertIn("MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS:-30", self.workflow)

    def test_mcp_defaults_allow_no_public_hosts_and_only_the_agent_tools_credential(self):
        # 现有 MCP 都经 agent-tools 网关：默认不放行公网主机，凭证引用只有部署时追加的产品服务密钥。
        self.assertIn('MCP_ALLOWED_HOSTS: str = os.getenv("MCP_ALLOWED_HOSTS", "")', self.app_config)
        self.assertIn(
            'MCP_ALLOWED_CREDENTIAL_REFS: str = os.getenv("MCP_ALLOWED_CREDENTIAL_REFS", "")', self.app_config
        )
        self.assertIn("MCP_ALLOWED_HOSTS=\n", self.env_example)
        self.assertIn("MCP_ALLOWED_CREDENTIAL_REFS=AGENT_TOOLS_API_KEY\n", self.env_example)
        self.assertIn("MCP_ALLOWED_HOSTS:-}}", self.workflow)
        self.assertIn("MCP_ALLOWED_CREDENTIAL_REFS:-}}", self.workflow)
