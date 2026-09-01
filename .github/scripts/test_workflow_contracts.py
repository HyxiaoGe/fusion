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
        self.assertEqual(jobs["required"]["needs"], ["changes", "api", "ui"])
        self.assertEqual(jobs["required"]["if"], "always()")
        self.assertEqual(jobs["required"]["name"], "Fusion required gate")

    def test_gate_rejects_expected_failure_and_accepts_frontend_only(self) -> None:
        gate_script = self.ci["jobs"]["required"]["steps"][0]["run"]

        def run(values: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-eu", "-o", "pipefail", "-c", gate_script],
                env={**os.environ, **values},
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

    def test_manual_rollback_contract_keeps_application_targets_independent(self) -> None:
        dispatch = self.dispatch["workflow_dispatch"]
        self.assertEqual(dispatch["target"]["allowed"], ["api", "ui", "both"])
        self.assertTrue(dispatch["api_rollback_sha"]["independent"])
        self.assertTrue(dispatch["ui_rollback_sha"]["independent"])
        self.assertTrue(self.dispatch["behavior"]["manual_target_bypasses_path_filter"])
        self.assertEqual(self.dispatch["behavior"]["both_order"], ["api", "ui"])
        self.assertTrue(self.dispatch["behavior"]["stop_on_api_failure"])


if __name__ == "__main__":
    unittest.main()
