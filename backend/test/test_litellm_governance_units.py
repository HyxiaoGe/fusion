import subprocess
import sys
import unittest
from pathlib import Path

if __package__:
    from .workflow_script_test_support import read_expanded_workflow
else:
    from workflow_script_test_support import read_expanded_workflow

ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = ROOT.parent
COST_SERVICE = ROOT / "ops/litellm/fusion-litellm-cost-sync.service"
COST_TIMER = ROOT / "ops/litellm/fusion-litellm-cost-sync.timer"
REQUIREMENTS = ROOT / "ops/litellm/requirements-governance.txt"
GOVERNANCE_ENV = ROOT / "ops/litellm/litellm-governance.env.example"
DEPLOY_WORKFLOW = MONOREPO_ROOT / ".github/workflows/_deploy-api.yml"


class LiteLLMCostSyncUnitTests(unittest.TestCase):
    def test_deploy_installs_cost_sync_and_retires_admission_units(self):
        content = read_expanded_workflow(DEPLOY_WORKFLOW)
        install_step = content.split("- name: Install LiteLLM cost sync", 1)[1].split("- name: ", 1)[0]

        self.assertIn("systemctl --user enable --now fusion-litellm-cost-sync.timer", install_step)
        self.assertIn("for retired_unit in fusion-litellm-governance fusion-litellm-model-management; do", install_step)
        self.assertIn('systemctl --user disable --now "${retired_unit}.timer"', install_step)
        self.assertNotIn("enable --now fusion-litellm-governance.timer", install_step)
        self.assertNotIn("fusion-litellm-model-management.timer", content.replace(install_step, ""))

    def test_documented_module_entrypoints_start_from_repo_root(self):
        modules = (
            "scripts.run_litellm_governance_unit",
            "scripts.ensure_litellm_cost_map_sync",
            "scripts.model_onboard",
        )

        for module in modules:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cost_sync_unit_is_separate_idempotent_apply_guard(self):
        service = COST_SERVICE.read_text(encoding="utf-8")
        timer = COST_TIMER.read_text(encoding="utf-8")

        self.assertIn("ensure_litellm_cost_map_sync.py", service)
        self.assertIn("--apply", service)
        self.assertNotIn("EnvironmentFile=", service)
        self.assertIn("%h/project/litellm-proxy/.env", service)
        self.assertIn("run_litellm_governance_unit.py", service)
        self.assertIn("-m scripts.run_litellm_governance_unit", service)
        self.assertIn("UnsetEnvironment=PYTHONPATH PYTHONHOME LD_PRELOAD", service)
        self.assertIn("AssertPathExists=", service)
        self.assertNotIn("ConditionPathExists=", service)
        self.assertIn("litellm-governance-venv/bin/python", service)
        self.assertNotIn("sk-", service)
        self.assertNotIn("/usr/bin/python3", service)
        self.assertIn("%h/.local/share/fusion/litellm-governance-current", service)
        self.assertNotIn("%h/project/fusion/fusion-api", service)
        self.assertIn("*:00/15:00 Asia/Shanghai", timer)
        self.assertNotIn("OnUnitActiveSec=", timer)
        self.assertIn("Persistent=true", timer)

    def test_host_runtime_dependency_is_version_pinned(self):
        content = REQUIREMENTS.read_text(encoding="utf-8")

        self.assertIn("httpx==0.28.1", content)
        self.assertNotIn(">=", content)

    def test_governance_env_does_not_duplicate_provider_or_master_keys(self):
        content = GOVERNANCE_ENV.read_text(encoding="utf-8")

        self.assertIn("LITELLM_BASE_URL=", content)
        self.assertNotIn("LITELLM_MASTER_KEY=", content)
        self.assertNotIn("MOONSHOT_API_KEY=", content)
        self.assertNotIn("QWEN_API_KEY=", content)


if __name__ == "__main__":
    unittest.main()
