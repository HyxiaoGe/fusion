import os
import subprocess
import unittest
from pathlib import Path

import yaml

from deploy_workflow_test_support import expand_deploy_scripts


ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github/workflows/pr-ci.yml"
API_WRAPPER_PATH = ROOT / ".github/workflows/_deploy-api.yml"
UI_WRAPPER_PATH = ROOT / ".github/workflows/_deploy-ui.yml"
APP_WORKFLOW_PATH = ROOT / ".github/workflows/_deploy-app.yml"
ORCHESTRATOR_PATH = ROOT / ".github/workflows/deploy-dev.yml"
DISPATCH_CONTRACT_PATH = ROOT / ".github/contracts/deploy-dispatch.yml"
ACTIONLINT_CONFIG_PATH = ROOT / ".github/actionlint.yaml"


def load_workflow(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path in (API_WRAPPER_PATH, UI_WRAPPER_PATH):
        text = expand_deploy_scripts(text, ROOT)
    return yaml.safe_load(text)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ci_text = CI_PATH.read_text(encoding="utf-8")
        cls.ci = load_workflow(CI_PATH)
        cls.api_text = expand_deploy_scripts(API_WRAPPER_PATH.read_text(encoding="utf-8"), ROOT)
        cls.ui_text = expand_deploy_scripts(UI_WRAPPER_PATH.read_text(encoding="utf-8"), ROOT)
        cls.orchestrator_text = (
            ORCHESTRATOR_PATH.read_text(encoding="utf-8") if ORCHESTRATOR_PATH.exists() else ""
        )
        cls.orchestrator = load_workflow(ORCHESTRATOR_PATH) if ORCHESTRATOR_PATH.exists() else {}
        cls.api = load_workflow(API_WRAPPER_PATH)
        cls.ui = load_workflow(UI_WRAPPER_PATH)
        cls.api_raw = yaml.safe_load(API_WRAPPER_PATH.read_text(encoding="utf-8"))
        cls.ui_raw = yaml.safe_load(UI_WRAPPER_PATH.read_text(encoding="utf-8"))
        cls.app = load_workflow(APP_WORKFLOW_PATH) if APP_WORKFLOW_PATH.exists() else {}
        cls.dispatch = load_workflow(DISPATCH_CONTRACT_PATH)
        cls.actionlint = load_workflow(ACTIONLINT_CONFIG_PATH)

    def test_reusable_deploy_wrappers_have_no_direct_trigger(self) -> None:
        for text, document in ((self.api_text, self.api), (self.ui_text, self.ui)):
            self.assertRegex(text, r"(?m)^on:\n  workflow_call:")
            self.assertNotRegex(text, r"(?m)^  (push|pull_request|workflow_dispatch):")
            self.assertIn("workflow_call", document[True])

        enabled_ci = self.ci_text
        self.assertNotIn("uses: ./.github/workflows/_deploy-api.yml", enabled_ci)
        self.assertNotIn("uses: ./.github/workflows/_deploy-ui.yml", enabled_ci)

    def test_application_jobs_are_independent_and_gate_is_constant(self) -> None:
        jobs = self.ci["jobs"]
        self.assertEqual(jobs["api"]["needs"], "changes")
        self.assertEqual(jobs["ui"]["needs"], "changes")
        self.assertEqual(
            jobs["required"]["needs"],
            ["changes", "workflow-security", "api", "ui"],
        )
        self.assertEqual(jobs["required"]["if"], "always()")
        self.assertEqual(jobs["required"]["name"], "Fusion required gate")

    def test_workflow_security_job_runs_actionlint_and_zizmor(self) -> None:
        job = self.ci["jobs"]["workflow-security"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["permissions"], {"contents": "read"})

        steps = {step["name"]: step for step in job["steps"]}
        install = steps["Install actionlint"]["run"]
        self.assertIn("ACTIONLINT_VERSION=1.7.12", install)
        self.assertIn(
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            install,
        )
        self.assertEqual(steps["Run actionlint"]["run"], "./.tools/actionlint -color")

        zizmor = steps["Run zizmor"]
        self.assertEqual(
            zizmor["uses"],
            "zizmorcore/zizmor-action@3dc1ecc9bcb9e94e9b2c709687979e1298497054",
        )
        self.assertEqual(zizmor["with"]["version"], "1.29.0")
        self.assertFalse(zizmor["with"]["advanced-security"])
        self.assertEqual(zizmor["with"]["min-severity"], "high")
        self.assertEqual(zizmor["with"]["min-confidence"], "high")

    def test_deploy_metrics_steps_do_not_expand_contexts_inside_shell(self) -> None:
        for workflow in (self.api, self.ui):
            steps = workflow["jobs"]["deploy-dev"]["steps"]
            metrics = next(step for step in steps if step["name"] == "Push CI/CD metrics")
            self.assertNotIn("${{", metrics["run"])
            self.assertEqual(metrics["env"]["METRICS_REF_NAME"], "${{ github.ref_name }}")
            self.assertEqual(metrics["env"]["METRICS_JOB_STATUS"], "${{ job.status }}")

    def test_actionlint_config_only_suppresses_inherited_wrapper_shell_warnings(self) -> None:
        self.assertEqual(
            self.actionlint["self-hosted-runner"]["labels"],
            ["fusion-api", "fusion-ui"],
        )
        paths = self.actionlint["paths"]
        self.assertEqual(
            set(paths),
            {
                ".github/workflows/_deploy-api.yml",
                ".github/workflows/_deploy-ui.yml",
            },
        )
        for config in paths.values():
            for pattern in config["ignore"]:
                self.assertIn("shellcheck reported issue", pattern)

    def test_manual_ci_cannot_override_the_validated_commit_range(self) -> None:
        workflow_dispatch = self.ci[True]["workflow_dispatch"]
        self.assertIsNone(workflow_dispatch)
        self.assertNotIn("base_sha", self.ci_text)
        self.assertNotIn("head_sha", self.ci_text)
        self.assertNotIn("DISPATCH_BASE", self.ci_text)
        self.assertNotIn("DISPATCH_HEAD", self.ci_text)

    def test_gate_rejects_expected_failure_and_accepts_frontend_only(self) -> None:
        gate_script = self.ci["jobs"]["required"]["steps"][0]["run"]

        def run(values: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-eu", "-o", "pipefail", "-c", gate_script],
                env={
                    **os.environ,
                    "WORKFLOW_SECURITY_RESULT": "success",
                    **values,
                },
                text=True,
                capture_output=True,
            )

        frontend_only = run(
            {
                "CHANGES_RESULT": "success",
                "API_EXPECTED": "false",
                "API_RESULT": "skipped",
                "UI_EXPECTED": "true",
                "UI_RESULT": "success",
            }
        )
        self.assertEqual(frontend_only.returncode, 0, frontend_only.stderr)

        expected_api_failed = run(
            {
                "CHANGES_RESULT": "success",
                "API_EXPECTED": "true",
                "API_RESULT": "failure",
                "UI_EXPECTED": "false",
                "UI_RESULT": "skipped",
            }
        )
        self.assertNotEqual(expected_api_failed.returncode, 0)

        missing_api_decision = run(
            {
                "CHANGES_RESULT": "success",
                "API_EXPECTED": "",
                "API_RESULT": "skipped",
                "UI_EXPECTED": "false",
                "UI_RESULT": "skipped",
            }
        )
        self.assertNotEqual(missing_api_decision.returncode, 0)

        invalid_ui_decision = run(
            {
                "CHANGES_RESULT": "success",
                "API_EXPECTED": "false",
                "API_RESULT": "skipped",
                "UI_EXPECTED": "unexpected",
                "UI_RESULT": "skipped",
            }
        )
        self.assertNotEqual(invalid_ui_decision.returncode, 0)

        workflow_security_failed = run(
            {
                "WORKFLOW_SECURITY_RESULT": "failure",
                "CHANGES_RESULT": "success",
                "API_EXPECTED": "false",
                "API_RESULT": "skipped",
                "UI_EXPECTED": "false",
                "UI_RESULT": "skipped",
            }
        )
        self.assertNotEqual(workflow_security_failed.returncode, 0)

    def test_manual_rollback_contract_keeps_application_targets_independent(self) -> None:
        dispatch = self.dispatch["workflow_dispatch"]
        self.assertEqual(dispatch["target"]["allowed"], ["api", "ui", "both"])
        self.assertTrue(dispatch["api_rollback_sha"]["independent"])
        self.assertTrue(dispatch["ui_rollback_sha"]["independent"])
        self.assertTrue(self.dispatch["behavior"]["manual_target_bypasses_path_filter"])
        self.assertEqual(self.dispatch["behavior"]["both_order"], ["api", "ui"])
        self.assertTrue(self.dispatch["behavior"]["stop_on_api_failure"])

    def test_future_deployment_contract_uses_digest_and_global_concurrency(self) -> None:
        identity = self.dispatch["release_identity"]
        self.assertEqual(identity["deploy_ref"], "repository_digest")
        self.assertEqual(identity["sha_tag_role"], "audit_alias_only")
        self.assertEqual(identity["rollback_lookup"], "per_app_release_ledger")
        self.assertTrue(identity["reject_unresolved_digest"])

        concurrency = self.dispatch["concurrency"]
        self.assertEqual(concurrency["owner"], "orchestrator")
        self.assertEqual(concurrency["group"], "fusion-dev")
        self.assertFalse(concurrency["cancel_in_progress"])
        self.assertFalse(concurrency["wrappers_define_concurrency"])
        self.assertNotIn("concurrency", self.api)
        self.assertNotIn("concurrency", self.ui)
        self.assertNotIn("不可变镜像 SHA", self.ui_text)
        self.assertNotIn("不可变 SHA 标签", self.ui_text)

    def test_task2_orchestrator_serializes_api_then_ui(self) -> None:
        self.assertTrue(ORCHESTRATOR_PATH.exists(), "Task 2 必须启用独立 dev orchestrator")
        triggers = self.orchestrator[True]
        self.assertEqual(triggers["push"]["branches"], ["master"])
        dispatch = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(dispatch["target"]["options"], ["api", "ui", "both"])

        concurrency = self.orchestrator["concurrency"]
        self.assertEqual(concurrency["group"], "fusion-dev")
        self.assertFalse(concurrency["cancel-in-progress"])

        jobs = self.orchestrator["jobs"]
        self.assertEqual(jobs["deploy-api"]["needs"], "changes")
        self.assertEqual(jobs["deploy-api"]["uses"], "./.github/workflows/_deploy-app.yml")
        self.assertEqual(jobs["deploy-api"]["secrets"], "inherit")
        self.assertEqual(jobs["deploy-ui"]["needs"], ["changes", "deploy-api"])
        self.assertEqual(jobs["deploy-ui"]["uses"], "./.github/workflows/_deploy-app.yml")
        self.assertEqual(jobs["deploy-ui"]["secrets"], "inherit")
        self.assertIn("always()", jobs["deploy-ui"]["if"])
        self.assertIn("needs.deploy-api.result == 'success'", jobs["deploy-ui"]["if"])

    def test_task4_parameterized_workflow_preserves_per_app_contracts(self) -> None:
        self.assertTrue(APP_WORKFLOW_PATH.exists(), "Task 4 必须提供参数化 _deploy-app.yml")
        inputs = self.app[True]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "app",
                "image_repository",
                "health_check_endpoint",
                "migration_enabled",
                "dependency_services",
                "rollback_anchor_policy",
                "deploy_sha",
                "rollback_sha",
                "rollback_reason",
            },
        )

        jobs = self.app["jobs"]
        self.assertEqual(jobs["deploy-api"]["uses"], "./.github/workflows/_deploy-api.yml")
        self.assertEqual(jobs["deploy-ui"]["uses"], "./.github/workflows/_deploy-ui.yml")
        self.assertIn("inputs.app == 'api'", jobs["deploy-api"]["if"])
        self.assertIn("inputs.app == 'ui'", jobs["deploy-ui"]["if"])

        for name, app, repository, endpoint, migrations, dependencies, policy in (
            (
                "deploy-api",
                "api",
                "seanfield/fusion-api",
                "http://127.0.0.1:8002/health",
                True,
                "postgres,redis,litellm,flyai-adapter,knowledge-worker",
                "api-and-adapter-image-identities",
            ),
            (
                "deploy-ui",
                "ui",
                "seanfield/fusion-ui",
                "http://127.0.0.1:3000/",
                False,
                "api",
                "ui-image-identity",
            ),
        ):
            with self.subTest(job=name):
                supplied = self.orchestrator["jobs"][name]["with"]
                self.assertEqual(supplied["app"], app)
                self.assertEqual(supplied["image_repository"], repository)
                self.assertEqual(supplied["health_check_endpoint"], endpoint)
                self.assertEqual(supplied["migration_enabled"], migrations)
                self.assertEqual(supplied["dependency_services"], dependencies)
                self.assertEqual(supplied["rollback_anchor_policy"], policy)

    def test_task4_parameters_drive_the_selected_implementation_hooks(self) -> None:
        parameter_names = {
            "app",
            "image_repository",
            "health_check_endpoint",
            "migration_enabled",
            "dependency_services",
            "rollback_anchor_policy",
        }
        for workflow in (self.api, self.ui):
            self.assertTrue(parameter_names.issubset(workflow[True]["workflow_call"]["inputs"]))

        for job in (self.app["jobs"]["deploy-api"], self.app["jobs"]["deploy-ui"]):
            self.assertTrue(parameter_names.issubset(job["with"]))

        api_env = self.api["env"]
        self.assertIn("inputs.image_repository", api_env["IMAGE_NAME"])
        self.assertEqual(api_env["API_HEALTH_CHECK_ENDPOINT"], "${{ inputs.health_check_endpoint }}")
        self.assertEqual(
            api_env.get("DEPLOY_HEALTH_CHECK_ENDPOINT"),
            "${{ inputs.health_check_endpoint }}",
        )
        self.assertEqual(api_env["DEPLOY_DEPENDENCY_SERVICES"], "${{ inputs.dependency_services }}")
        self.assertEqual(api_env["DEPLOY_ROLLBACK_ANCHOR_POLICY"], "${{ inputs.rollback_anchor_policy }}")
        api_migration = next(
            step
            for step in self.api["jobs"]["deploy-dev"]["steps"]
            if step["name"] == "Apply alembic migrations"
        )
        self.assertIn("inputs.migration_enabled", api_migration["if"])

        ui_env = self.ui["env"]
        self.assertIn("inputs.image_repository", ui_env["IMAGE_NAME"])
        self.assertEqual(ui_env["UI_HEALTH_CHECK_ENDPOINT"], "${{ inputs.health_check_endpoint }}")
        self.assertEqual(
            ui_env.get("DEPLOY_HEALTH_CHECK_ENDPOINT"),
            "${{ inputs.health_check_endpoint }}",
        )
        self.assertEqual(ui_env["DEPLOY_DEPENDENCY_SERVICES"], "${{ inputs.dependency_services }}")
        self.assertEqual(ui_env["DEPLOY_ROLLBACK_ANCHOR_POLICY"], "${{ inputs.rollback_anchor_policy }}")
        self.assertIn('curl -fsS "${API_HEALTH_CHECK_ENDPOINT}"', self.api_text)
        self.assertIn('process.env.FUSION_UI_HEALTH_CHECK_ENDPOINT', self.ui_text)

    def test_task4_direct_app_calls_require_the_same_fail_closed_contract(self) -> None:
        parameter_names = {
            "app",
            "image_repository",
            "health_check_endpoint",
            "migration_enabled",
            "dependency_services",
            "rollback_anchor_policy",
        }
        for workflow in (self.api, self.ui):
            direct_inputs = workflow[True]["workflow_call"]["inputs"]
            for name in parameter_names:
                with self.subTest(workflow=workflow.get("name"), parameter=name):
                    self.assertTrue(direct_inputs[name]["required"])
                    self.assertNotIn("default", direct_inputs[name])

        api_prepare = self.api_raw["jobs"]["prepare"]
        api_steps = {step["name"]: step for step in api_prepare["steps"] if "name" in step}
        self.assertEqual(
            api_steps["Validate application deployment contract"]["run"],
            "ops/deploy/validate-app-deployment-contract.sh",
        )

        ui_validate = self.ui_raw["jobs"]["validate-parameters"]
        self.assertEqual(ui_validate["runs-on"], "ubuntu-latest")
        ui_step = ui_validate["steps"][-1]
        self.assertEqual(ui_step["run"], "ops/deploy/validate-app-deployment-contract.sh")
        self.assertEqual(self.ui_raw["jobs"]["publish"]["needs"], "validate-parameters")
        self.assertEqual(
            self.ui_raw["jobs"]["deploy-dev"]["needs"],
            ["validate-parameters", "publish"],
        )
        self.assertIn(
            "needs.validate-parameters.result == 'success'",
            self.ui_raw["jobs"]["deploy-dev"]["if"],
        )

        contracts = self.dispatch["parameterized_workflow"]
        self.assertEqual(contracts["path"], ".github/workflows/_deploy-app.yml")
        self.assertEqual(contracts["app_hooks"]["api"], ".github/workflows/_deploy-api.yml")
        self.assertEqual(contracts["app_hooks"]["ui"], ".github/workflows/_deploy-ui.yml")
        self.assertEqual(contracts["direct_call_contract"], "required_and_fail_closed")

    def test_task2_uses_digest_ledger_and_checkout_independent_runtime_paths(self) -> None:
        combined = self.api_text + "\n" + self.ui_text
        for legacy in (
            "cd ~/project/fusion",
            "source .env",
            "--env-file .env",
            "./fusion-api/storage/files",
        ):
            self.assertNotIn(legacy, combined)

        self.assertIn('config_dir="${HOME}/.config/fusion"', self.api_text)
        self.assertIn('runtime_env="${config_dir}/runtime.env"', self.api_text)
        self.assertIn("${HOME}/.local/share/fusion/api/storage/files", self.api_text)
        self.assertIn("${HOME}/.local/share/fusion/api/runtime", self.api_text)
        self.assertIn("${HOME}/.local/share/fusion/ui/runtime", self.ui_text)
        self.assertIn("storage.upload(key, payload", self.api_text)
        self.assertIn("storage.download(key)", self.api_text)
        self.assertIn("storage.delete(key)", self.api_text)
        self.assertIn("systemctl --user enable --now fusion-litellm-cost-sync.timer", self.api_text)
        self.assertIn("ExecMainStatus --value", self.api_text)
        for wrapper in (self.api_text, self.ui_text):
            self.assertIn(".github/scripts/release_ledger.py", wrapper)
            self.assertIn("docker buildx imagetools inspect", wrapper)
            self.assertIn("@sha256:", wrapper)

    def test_task2_compose_handoff_keeps_legacy_project_identity(self) -> None:
        api_compose_calls = [
            line.strip()
            for line in self.api_text.splitlines()
            if "docker compose" in line and "docker-compose.fusion-api-ghcr.yml" in line
        ]
        ui_compose_calls = [
            line.strip()
            for line in self.ui_text.splitlines()
            if "docker compose" in line and "docker-compose.fusion-ui-ghcr.yml" in line
        ]

        self.assertGreaterEqual(len(api_compose_calls), 4)
        self.assertGreaterEqual(len(ui_compose_calls), 2)
        for command in (*api_compose_calls, *ui_compose_calls):
            self.assertIn("--project-name fusion", command)

        self.assertNotIn("兜底：清掉旧 compose project", self.ui_text)


if __name__ == "__main__":
    unittest.main()
