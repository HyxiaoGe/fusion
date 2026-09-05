"""锁住 P0 过渡门禁开关的发布链路。

仅定义 Settings 字段不构成可执行门禁：值必须真正下发到容器，否则容器永远用默认
false，过渡完成后正常热更新会被门禁永久拦住。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = ROOT.parent
FLAG = "PROMPT_P0_BASELINE_ATTESTED"


class PromptP0DeployConfigTests(unittest.TestCase):
    def test_deploy_workflow_passes_flag_to_both_jobs(self):
        workflow = (MONOREPO_ROOT / ".github/workflows/_deploy-api.yml").read_text(encoding="utf-8")

        self.assertEqual(
            workflow.count(f"DEPLOY_{FLAG}: " + "${{ vars." + FLAG + " || 'false' }}"),
            2,
            "部署与回滚两处 job 都必须下发该开关",
        )

    def test_pull_and_restart_exports_and_injects_flag(self):
        script = (MONOREPO_ROOT / "ops/deploy/api-pull-and-restart.sh").read_text(encoding="utf-8")

        self.assertIn(f'export {FLAG}="${{DEPLOY_{FLAG}:-false}}"', script)
        self.assertIn(f"      - {FLAG}=${{{FLAG}:-false}}", script)

    def test_rollback_script_exports_flag(self):
        """回滚后若丢失该值，已 attested 的环境会退回被门禁拦住的状态。"""

        script = (MONOREPO_ROOT / "ops/deploy/api-rollback-failed-deployment.sh").read_text(encoding="utf-8")

        self.assertIn(f'export {FLAG}="${{DEPLOY_{FLAG}:-false}}"', script)

    def test_health_check_reports_flag_from_inside_container(self):
        script = (MONOREPO_ROOT / "ops/deploy/api-verify-health.sh").read_text(encoding="utf-8")

        self.assertIn(f"settings.{FLAG}", script)
        self.assertIn("prompt P0 baseline gate", script)

    def test_flag_defaults_to_false_everywhere(self):
        """默认必须是 false：门禁只能显式解除，不能因为漏配而失效。"""

        from app.core.config import settings

        self.assertIs(settings.PROMPT_P0_BASELINE_ATTESTED, False)


if __name__ == "__main__":
    unittest.main()
