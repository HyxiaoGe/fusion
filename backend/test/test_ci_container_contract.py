import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = ROOT.parent


class CIContainerContractTest(unittest.TestCase):
    def test_linux_ci_container_mounts_monorepo_test_inputs_read_only(self) -> None:
        source_build_script = ROOT / ".github/scripts/linux-build-and-test.sh"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            monorepo_root = temp_root / "monorepo root with spaces"
            script_directory = monorepo_root / "backend/.github/scripts"
            script_directory.mkdir(parents=True)
            build_script = script_directory / "linux-build-and-test.sh"
            build_script.write_text(source_build_script.read_text(encoding="utf-8"), encoding="utf-8")
            preflight_directory = monorepo_root / "backend/scripts"
            preflight_directory.mkdir(parents=True)
            preflight = preflight_directory / "check_frozen_prompt_fixture_bytes.py"
            preflight.write_text(
                (ROOT / "scripts/check_frozen_prompt_fixture_bytes.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            fixture_directory = monorepo_root / "backend/test/fixtures/prompt_bundle"
            fixture_directory.mkdir(parents=True)
            fixture = fixture_directory / "p3a_sync.py"
            fixture.write_bytes((ROOT / "test/fixtures/prompt_bundle/p3a_sync.py").read_bytes())
            legacy_fixture = fixture_directory / "legacy_v2_contract.json"
            legacy_fixture.write_bytes(
                (ROOT / "test/fixtures/prompt_bundle/legacy_v2_contract.json").read_bytes()
            )
            bin_dir = temp_root / "bin"
            bin_dir.mkdir()
            docker_log = temp_root / "docker.log"
            fake_docker = bin_dir / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "printf '<call>\\n' >> \"${DOCKER_LOG}\"\n"
                'printf \'%s\\n\' "$@" >> "${DOCKER_LOG}"\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            env = os.environ.copy()
            env["DOCKER_LOG"] = str(docker_log)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            subprocess.run(
                ["bash", str(build_script), "fusion-api-ci", "fusion-adapter-ci", "test-sha"],
                cwd=monorepo_root,
                env=env,
                check=True,
            )

            docker_calls = docker_log.read_text(encoding="utf-8")

        self.assertIn(
            f"type=bind,source={monorepo_root}/.github,target=/.github,readonly",
            docker_calls.splitlines(),
        )
        self.assertIn(
            f"type=bind,source={monorepo_root}/ops,target=/ops,readonly",
            docker_calls.splitlines(),
        )

    def test_development_dependencies_cover_runtime_and_ci(self) -> None:
        development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

        self.assertIn("-r requirements.txt", development)
        self.assertIn("-r requirements-ci.txt", development)
        self.assertIn("pytest==9.1.1", development)

    def test_ci_dependencies_are_separate_from_production(self) -> None:
        production = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        ci = (ROOT / "requirements-ci.txt").read_text(encoding="utf-8")

        self.assertNotIn("fakeredis", production)
        self.assertNotIn("-r requirements.txt", ci)
        self.assertIn("fakeredis[lua]", ci)
        self.assertIn("pytest==9.1.1", ci)
        self.assertIn("ruff==", ci)

    def test_auth_client_uses_fixed_pypi_release(self) -> None:
        production = (ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("seanfield-auth-client[fastapi]==0.3.1", production)
        self.assertNotIn("git+https://github.com/HyxiaoGe/auth-service", production)
        self.assertNotIn("#subdirectory=auth-client", production)

    def test_dockerfile_exposes_dependencies_and_production_targets(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("AS dependencies", dockerfile)
        self.assertIn("AS production", dockerfile)
        self.assertIn("COPY requirements-ci.txt", dockerfile)
        self.assertNotIn("python -m unittest discover", dockerfile)

        production = dockerfile[dockerfile.index("AS production") :]
        self.assertNotIn("build-essential", production)
        self.assertNotIn("gcc", production)
        self.assertNotIn("ruff", production)
        self.assertNotRegex(dockerfile, r"(?m)^\s*git\s*\\?\s*$")

    def test_dockerfile_retries_transient_apt_download_failures(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dependency_stage = dockerfile[: dockerfile.index("AS production")]

        self.assertGreaterEqual(dependency_stage.count("Acquire::Retries=5"), 2)
        self.assertIn("for attempt in 1 2 3 4 5", dependency_stage)
        self.assertIn('if [ "$attempt" -eq 5 ]', dependency_stage)

    def test_dockerfile_retries_transient_pypi_download_failures(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pip_install = dockerfile[dockerfile.index("COPY requirements.txt") : dockerfile.index("AS production")]

        self.assertIn("for attempt in 1 2 3 4 5", pip_install)
        self.assertIn("pip install --retries 5 --timeout 60 -r requirements.txt", pip_install)
        self.assertIn('if [ "$attempt" -eq 5 ]', pip_install)

    def test_pr_and_release_workflows_run_equivalent_container_tests(self) -> None:
        release_workflow = (MONOREPO_ROOT / ".github/workflows/_deploy-api.yml").read_text(encoding="utf-8")
        pr_workflow = (MONOREPO_ROOT / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
        windows_build_script = (ROOT / ".github/scripts/windows-build-and-test.ps1").read_text(encoding="utf-8")
        linux_build_script = (ROOT / ".github/scripts/linux-build-and-test.sh").read_text(encoding="utf-8")

        self.assertIn("backend/.github/scripts/windows-build-and-test.ps1", release_workflow)
        self.assertIn("backend/.github/scripts/linux-build-and-test.sh", pr_workflow)
        for build_script in (windows_build_script, linux_build_script):
            self.assertIn("docker build --target production", build_script)
            self.assertIn(
                "pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt",
                build_script,
            )
            self.assertIn("python scripts/check_architecture.py", build_script)
            self.assertIn("ruff check .", build_script)
            self.assertIn("python -u -m unittest discover -s test -t . -v", build_script)
            self.assertIn(
                "python -m pytest -q test/services/stream/test_run_capability_router.py",
                build_script,
            )

        self.assertIn(
            '--mount "type=bind,source=${app_root}/README.md,target=/app/README.md,readonly"',
            linux_build_script,
        )
        self.assertIn(
            '--mount "type=bind,source=$appRoot\\README.md,target=/app/README.md,readonly"',
            windows_build_script,
        )
        self.assertIn(
            '--mount "type=bind,source=$monorepoRoot\\.github,target=/.github,readonly"',
            windows_build_script,
        )
        self.assertIn(
            '--mount "type=bind,source=$monorepoRoot\\ops,target=/ops,readonly"',
            windows_build_script,
        )

    def test_frozen_prompt_fixture_preflight_runs_before_any_docker_build(self) -> None:
        commands = {
            "linux-build-and-test.sh": "scripts/check_frozen_prompt_fixture_bytes.py",
            "windows-build-and-test.ps1": "check-frozen-prompt-fixture-bytes.ps1",
        }
        for filename, command in commands.items():
            script = (ROOT / ".github/scripts" / filename).read_text(encoding="utf-8")
            with self.subTest(script=filename):
                self.assertIn(command, script)
                self.assertLess(script.index(command), script.index("docker build"))

    def test_prompt_freeze_contracts_run_in_both_container_entrypoints(self) -> None:
        required_tests = (
            "test/test_prompt_bundle_snapshot.py",
            "test/services/stream/test_prompt_run_identity.py",
            "test/services/stream/test_run_prompt_snapshot.py",
            "test/test_prompt_bundle_v2_integrity.py",
            "test/test_prompt_bundle_v2_transactions.py",
            "test/test_prompt_bundle_bridge.py",
            "test/test_prompt_bundle_bridge_cli.py",
            "test/test_prompt_bundle_bridge_deployment.py",
            "test/test_prompt_bundle_hold.py",
            "test/test_prompt_bundle_hold_api.py",
            "test/test_prompt_bundle_hold_migration.py",
            "test/test_frozen_prompt_fixture_bytes.py",
            "test/test_prompt_bundle_hold_preflight.py",
            "test/test_prompt_template_engine.py",
            "test/test_prompt_engine_policy.py",
            "test/test_prompt_engine_deployment.py",
            "test/test_prompt_jinja_only.py",
            "test/test_prompt_bundle_hold_deployment.py",
        )
        for filename in ("linux-build-and-test.sh", "windows-build-and-test.ps1"):
            script = (ROOT / ".github/scripts" / filename).read_text(encoding="utf-8")
            pytest_command = next(line for line in script.splitlines() if "python -m pytest" in line)
            for test_file in required_tests:
                with self.subTest(script=filename, test_file=test_file):
                    self.assertIn(test_file, pytest_command)

    def test_windows_release_build_disables_registry_incompatible_attestations(self) -> None:
        windows_build_script = (ROOT / ".github/scripts/windows-build-and-test.ps1").read_text(encoding="utf-8")
        build_commands = [
            line.strip() for line in windows_build_script.splitlines() if line.strip().startswith("docker build ")
        ]

        self.assertEqual(3, len(build_commands))
        for command in build_commands:
            self.assertIn("--provenance=false", command)

    def test_windows_release_normalizes_bash_contract_before_running_tests(self) -> None:
        windows_build_script = (ROOT / ".github/scripts/windows-build-and-test.ps1").read_text(encoding="utf-8")

        self.assertIn("linux-build-and-test.sh", windows_build_script)
        self.assertIn('.Replace("`r`n", "`n").Replace("`r", "`n")', windows_build_script)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", windows_build_script)
        self.assertIn(
            "target=/app/.github/scripts/linux-build-and-test.sh,readonly",
            windows_build_script,
        )
        self.assertIn("finally", windows_build_script)
        self.assertIn("Remove-Item -LiteralPath $normalizedLinuxScript", windows_build_script)


if __name__ == "__main__":
    unittest.main()
