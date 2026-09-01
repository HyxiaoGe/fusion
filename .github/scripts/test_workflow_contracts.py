import os
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github/workflows/pr-ci.yml"
API_WRAPPER_PATH = ROOT / ".github/workflows/_deploy-api.yml"
UI_WRAPPER_PATH = ROOT / ".github/workflows/_deploy-ui.yml"
DISPATCH_CONTRACT_PATH = ROOT / ".github/contracts/deploy-dispatch.yml"
ACTIONLINT_CONFIG_PATH = ROOT / ".github/actionlint.yaml"


def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ci_text = CI_PATH.read_text(encoding="utf-8")
        cls.ci = load_workflow(CI_PATH)
        cls.api_text = API_WRAPPER_PATH.read_text(encoding="utf-8")
        cls.ui_text = UI_WRAPPER_PATH.read_text(encoding="utf-8")
        cls.api = load_workflow(API_WRAPPER_PATH)
        cls.ui = load_workflow(UI_WRAPPER_PATH)
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


if __name__ == "__main__":
    unittest.main()
