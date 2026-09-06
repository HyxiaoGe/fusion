"""目标镜像检查失败必须阻止部署和恢复；不启动真实 Docker。"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("target", ["fixture/api@sha256:" + "a" * 64, "running"])
@pytest.mark.parametrize("exit_code", [0, 17])
def test_helper_executes_current_preflight_against_exact_target(tmp_path, target, exit_code):
    helper = ROOT / "ops/deploy/api-check-prompt-hold-target.sh"
    assert helper.is_file()
    binary = tmp_path / "docker"
    binary.write_text("""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
source = sys.stdin.read()
assert "def verify_prompt_hold_target" in source
assert "freeze_prompt_bundle" in source
assert "--env-file" not in args
if os.environ["TARGET"] == "running":
    assert args == ["exec", "-i", "fusion-api", "python", "-"]
else:
    assert args[0] == "run" and "--rm" in args and "postgres_default" in args
    assert args[-2] == os.environ["TARGET"] and args[-1] == "-"
    assert "DATABASE_URL" in args
sys.exit(int(os.environ["RETURN_CODE"]))
""")
    binary.chmod(0o755)
    result = subprocess.run(
        ["bash", str(helper), target],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TARGET": target,
            "RETURN_CODE": str(exit_code),
            "GITHUB_WORKSPACE": str(ROOT),
            "DATABASE_URL": "sqlite://",
            "OPS_DEPLOY_DRY_RUN": "false",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == exit_code, result.stderr


def test_both_forward_and_failure_rollback_check_target_before_replacement():
    forward = (ROOT / "ops/deploy/api-pull-and-restart.sh").read_text()
    rollback = (ROOT / "ops/deploy/api-rollback-failed-deployment.sh").read_text()
    check = '"${GITHUB_WORKSPACE}/ops/deploy/api-check-prompt-hold-target.sh" "${DEPLOY_API_IMAGE}"'
    assert forward.index(check) < forward.index(" up -d")
    assert rollback.index(
        'ensure_rollback_image "${ROLLBACK_API_IMAGE_REF}" "${ROLLBACK_API_IMAGE_ID}"'
    ) < rollback.index(check)
    assert rollback.index(check) < rollback.index(" up -d")
    assert '"${GITHUB_WORKSPACE}/ops/deploy/api-check-prompt-hold-target.sh" running' in rollback


def test_held_smoke_accepts_remote_failure_only_after_true_local_revalidation():
    import importlib.util
    from unittest.mock import AsyncMock, Mock, patch

    script = ROOT / "ops/deploy/prompt-bundle-smoke.py"
    assert script.is_file()
    spec = importlib.util.spec_from_file_location("hold_aware_bundle_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    held = {"state": "held", "revision": "a" * 64, "source_kind": "prompthub_lkg"}
    verifier = Mock(return_value=held)
    with patch(
        "app.services.prompthub_sync_service.sync_prompthub_bundle", AsyncMock(return_value={"status": "error"})
    ):
        assert module.try_verify_held_bundle(held, verifier) is True
    verifier.assert_called_once_with()
    broken = Mock(side_effect=ValueError("冻结失败"))
    with patch(
        "app.services.prompthub_sync_service.sync_prompthub_bundle", AsyncMock(return_value={"status": "error"})
    ):
        with pytest.raises(ValueError, match="冻结失败"):
            module.try_verify_held_bundle(held, broken)
