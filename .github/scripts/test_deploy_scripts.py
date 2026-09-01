import csv
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy_workflow_test_support import BODY_MARKER, deploy_script_body


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "ops/deploy/tests/fixtures/deploy-script-contracts.tsv"
FIXTURE_BIN = ROOT / "ops/deploy/tests/fixtures/bin"
PR_WORKFLOW = ROOT / ".github/workflows/pr-ci.yml"
WRAPPERS = (
    ROOT / ".github/workflows/_deploy-api.yml",
    ROOT / ".github/workflows/_deploy-ui.yml",
)


def contract_rows() -> list[dict[str, str]]:
    with FIXTURE_PATH.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def referenced_scripts(workflow: Path) -> dict[str, str]:
    references: dict[str, str] = {}
    step_name = ""
    for line in workflow.read_text(encoding="utf-8").splitlines():
        if line.startswith("      - name: "):
            step_name = line.removeprefix("      - name: ")
        match = re.match(r"^\s+run: (ops/deploy/[^\s]+\.sh)$", line)
        if match is not None:
            references[step_name] = match.group(1)
    return references


def multiline_runs(workflow: Path, jobs: set[str]) -> list[str]:
    offenders: list[str] = []
    in_jobs = False
    job = ""
    step_name = ""
    for line in workflow.read_text(encoding="utf-8").splitlines():
        if line == "jobs:":
            in_jobs = True
            continue
        if in_jobs:
            job_match = re.match(r"^  ([A-Za-z0-9_-]+):$", line)
            if job_match is not None:
                job = job_match.group(1)
            if line.startswith("      - name: "):
                step_name = line.removeprefix("      - name: ")
            if job in jobs and re.match(r"^\s+run: \|$", line):
                offenders.append(f"{job}: {step_name}")
    return offenders


def scripts_before_checkout(workflow: Path) -> list[str]:
    offenders: list[str] = []
    job = ""
    step_name = ""
    checkout_seen = False
    for line in workflow.read_text(encoding="utf-8").splitlines():
        job_match = re.match(r"^  ([A-Za-z0-9_-]+):$", line)
        if job_match is not None:
            job = job_match.group(1)
            checkout_seen = False
        if line.startswith("      - name: "):
            step_name = line.removeprefix("      - name: ")
        if re.match(r"^\s+(?:- )?uses: actions/checkout@", line):
            checkout_seen = True
        if re.match(r"^\s+run: ops/deploy/[^\s]+\.sh$", line) and not checkout_seen:
            offenders.append(f"{job}: {step_name}")
    return offenders


class DeployScriptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = contract_rows()

    def test_every_bash_run_block_is_a_fixture_bound_script(self) -> None:
        fixture_references = {
            (row["workflow"], row["step"]): row["script"] for row in self.rows
        }
        actual_references: dict[tuple[str, str], str] = {}
        for wrapper in WRAPPERS:
            workflow = str(wrapper.relative_to(ROOT))
            actual_references.update(
                {(workflow, step): script for step, script in referenced_scripts(wrapper).items()}
            )

        self.assertEqual(actual_references, fixture_references)
        self.assertEqual(multiline_runs(WRAPPERS[0], {"prepare", "deploy-dev", "finalize"}), [])
        self.assertEqual(multiline_runs(WRAPPERS[1], {"deploy-dev"}), [])

    def test_every_script_job_checks_out_the_repository_first(self) -> None:
        for wrapper in WRAPPERS:
            with self.subTest(wrapper=wrapper.name):
                self.assertEqual(scripts_before_checkout(wrapper), [])

    def test_wrappers_stay_within_task3_line_budget(self) -> None:
        for wrapper in WRAPPERS:
            with self.subTest(wrapper=wrapper.name):
                self.assertLessEqual(len(wrapper.read_text(encoding="utf-8").splitlines()), 400)

    def test_script_bodies_match_the_reviewed_fixtures(self) -> None:
        for row in self.rows:
            script = ROOT / row["script"]
            with self.subTest(script=row["script"]):
                self.assertTrue(script.is_file())
                self.assertTrue(os.access(script, os.X_OK))
                content = script.read_text(encoding="utf-8")
                self.assertTrue(content.startswith("#!/usr/bin/env bash\nset -eo pipefail\n"))
                self.assertIn(BODY_MARKER, content)
                digest = hashlib.sha256(deploy_script_body(ROOT, row["script"]).encode()).hexdigest()
                self.assertEqual(digest, row["body_sha256"])
                syntax = subprocess.run(
                    ["bash", "-n", str(script)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_every_script_supports_side_effect_free_dry_run(self) -> None:
        for row in self.rows:
            with self.subTest(script=row["script"]):
                completed = subprocess.run(
                    [str(ROOT / row["script"])],
                    cwd=ROOT,
                    env={**os.environ, "OPS_DEPLOY_DRY_RUN": "true"},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(f"DRY-RUN {Path(row['script']).name}", completed.stdout)

    def test_github_actions_cannot_silently_enable_dry_run(self) -> None:
        for row in self.rows:
            with self.subTest(script=row["script"]):
                completed = subprocess.run(
                    [str(ROOT / row["script"])],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "GITHUB_ACTIONS": "true",
                        "OPS_DEPLOY_DRY_RUN": "true",
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("GitHub Actions 不允许部署脚本 dry-run", completed.stderr)

    def test_health_check_rejects_matching_body_when_curl_transfer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_bin = Path(directory)
            fixtures = {
                "curl": "#!/usr/bin/env bash\nprintf '{\"status\":\"healthy\"}\\n'\nexit 18\n",
                "sleep": "#!/usr/bin/env bash\nexit 0\n",
                "docker": (
                    "#!/usr/bin/env bash\n"
                    "if [ \"${1:-}\" = \"inspect\" ]; then\n"
                    "  printf '/app/storage/files\\n/var/lib/fusion/litellm-governance\\n'\n"
                    "fi\n"
                    "exit 0\n"
                ),
            }
            for name, content in fixtures.items():
                fixture = fixture_bin / name
                fixture.write_text(content, encoding="utf-8")
                fixture.chmod(0o755)

            completed = subprocess.run(
                [str(ROOT / "ops/deploy/api-verify-health.sh")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fixture_bin}{os.pathsep}{os.environ['PATH']}",
                    "KNOWLEDGE_WORKER_SUPPORTED": "false",
                    "ROLLBACK_REQUESTED": "false",
                },
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(completed.returncode, 0)

    def test_valid_targets_follow_the_normal_path(self) -> None:
        valid_sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            api = subprocess.run(
                [str(ROOT / "ops/deploy/api-validate-deployment-request.sh")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "EVENT_NAME": "push",
                    "REQUESTED_DEPLOY_SHA": valid_sha,
                    "REQUESTED_ROLLBACK_SHA": "",
                    "REQUESTED_ROLLBACK_REASON": "",
                    "GITHUB_OUTPUT": str(output),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(api.returncode, 0, api.stderr)
            self.assertIn(f"target_sha={valid_sha}", output.read_text(encoding="utf-8"))

        ui = subprocess.run(
            [str(ROOT / "ops/deploy/ui-validate-deployment-target.sh")],
            cwd=ROOT,
            env={
                **os.environ,
                "ROLLBACK_SHA": "",
                "ROLLBACK_REASON": "",
                "DEPLOY_TARGET_SHA": valid_sha,
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(ui.returncode, 0, ui.stderr)

    def test_invalid_target_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = subprocess.run(
                [str(ROOT / "ops/deploy/api-validate-deployment-request.sh")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "EVENT_NAME": "push",
                    "REQUESTED_DEPLOY_SHA": "A" * 40,
                    "REQUESTED_ROLLBACK_SHA": "",
                    "REQUESTED_ROLLBACK_REASON": "",
                    "GITHUB_OUTPUT": str(Path(directory) / "github-output"),
                },
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(api.returncode, 0)
        self.assertIn("deploy_sha 必须是 40 位小写 Git SHA", api.stdout)

        ui = subprocess.run(
            [str(ROOT / "ops/deploy/ui-validate-deployment-target.sh")],
            cwd=ROOT,
            env={
                **os.environ,
                "ROLLBACK_SHA": "",
                "ROLLBACK_REASON": "",
                "DEPLOY_TARGET_SHA": "short",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(ui.returncode, 0)
        self.assertIn("实际部署目标不是合法的 40 位小写十六进制提交 SHA", ui.stdout)

    def test_missing_rollback_anchor_fails_before_mutation(self) -> None:
        dry_env = {
            **os.environ,
            "PATH": f"{FIXTURE_BIN}{os.pathsep}{os.environ['PATH']}",
            "DEPLOY_TARGET_SHA": "a" * 40,
        }
        api = subprocess.run(
            [str(ROOT / "ops/deploy/api-capture-current-deployment.sh")],
            cwd=ROOT,
            env=dry_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(api.returncode, 0)
        self.assertIn("fusion-api 不存在，拒绝在缺少回滚目标时变更部署", api.stdout)

        ui = subprocess.run(
            [str(ROOT / "ops/deploy/ui-capture-current-deployment.sh")],
            cwd=ROOT,
            env=dry_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(ui.returncode, 0)
        self.assertIn("无法同时捕获 fusion-ui 当前镜像引用与镜像 ID", ui.stdout)

    def test_pr_ci_runs_deploy_script_contracts(self) -> None:
        self.assertIn(
            "run: python3 .github/scripts/test_deploy_scripts.py",
            PR_WORKFLOW.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
