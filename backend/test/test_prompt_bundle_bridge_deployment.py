"""发布前置失败必须阻止服务替换；使用假的 Docker 命令验证脚本边界。"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_prepare_runs_after_schema_and_before_restart_with_existing_credentials():
    workflow = yaml.safe_load((ROOT / ".github/workflows/_deploy-api.yml").read_text())
    steps = workflow["jobs"]["deploy-dev"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Apply alembic migrations") < names.index("Prepare verified Prompt bundle")
    assert names.index("Prepare verified Prompt bundle") < names.index("Pull and restart fusion-api")
    step = steps[names.index("Prepare verified Prompt bundle")]
    assert step["env"]["PREVIOUS_API_ID"] == "${{ steps.capture_rollback_target.outputs.api_image_id }}"
    assert "rollback_requested != 'true'" in step["if"]
    assert step["env"]["DEPLOY_PROMPTHUB_API_KEY"] == "${{ secrets.PROMPTHUB_API_KEY }}"


@pytest.mark.parametrize("old_status", ["running", "exited", "restarting"])
@pytest.mark.parametrize("failure", ["capture", "seed", "identity", "none"])
def test_prepare_failures_do_not_restart_or_remove_existing_service(tmp_path, failure, old_status):
    script = ROOT / "ops/deploy/api-prepare-prompt-bundle.sh"
    assert script.is_file()
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "calls"
    docker = binary / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ["DOCKER_CALLS"]).open("a") as stream:
    stream.write(json.dumps(args)+"\\n")
if args[0] == "exec":
    raise AssertionError("基线读取不得依赖旧容器正在运行")
elif args[:2] == ["inspect", "fusion-api"]:
    print(json.dumps([{
        "Image": "sha256:" + ("c" if os.environ["FAILURE"] == "identity" else "b") * 64,
        "Config": {"Image": "fixture/api@sha256:" + "b" * 64, "Env": [
            "DATABASE_URL=postgresql://original:literal'quote@db/fusion",
            "PROMPTHUB_PROJECT_SLUG=fusion", "PROMPTHUB_SYNC_MODE=apply",
            "PROMPT_P0_BASELINE_ATTESTED=true", "UNRELATED_SECRET=不得复制"
        ]},
        "State": {"Status": os.environ["OLD_STATUS"]}
    }]))
elif args[0] == "create":
    if "--env-file" in args:
        saved = Path(args[args.index("--env-file") + 1]).read_text()
        assert "DATABASE_URL=postgresql://original:literal'quote@db/fusion" in saved
        assert "UNRELATED_SECRET" not in saved
        assert "sha256:" + "b" * 64 in args
        print("baseline-by-test")
    else:
        assert os.environ["DATABASE_URL"] == "sqlite://"
        assert os.environ["PROMPTHUB_PROJECT_SLUG"] == "fusion"
        assert "DATABASE_URL" in args
        print("created-by-test")
elif args[0] == "start":
    if args[-1] == "baseline-by-test":
        if os.environ["FAILURE"] == "capture": sys.exit(12)
        print('{"source_kind":"prompthub_lkg"}')
    elif os.environ["FAILURE"] == "seed": sys.exit(13)
elif args[0] == "inspect": print("0")
""")
    docker.chmod(0o755)
    runtime = tmp_path / "runtime.env"
    runtime.write_text("DATABASE_URL='sqlite://'\nPROMPTHUB_PROJECT_SLUG='fusion'\n")
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "FAILURE": failure,
        "OLD_STATUS": old_status,
        "PREVIOUS_API_ID": "sha256:" + "b" * 64,
        "PREVIOUS_API_REF": "fixture/api@sha256:" + "b" * 64,
        "FUSION_RUNTIME_ENV": str(runtime),
        "DEPLOY_API_IMAGE": "fixture/api@sha256:" + "a" * 64,
        "DEPLOY_PROMPTHUB_SYNC_MODE": "apply",
        "DEPLOY_PROMPT_P0_BASELINE_ATTESTED": "true",
        "DEPLOY_PROMPTHUB_API_KEY": "测试凭据不得输出",
        "GITHUB_RUN_ID": "123",
        "OPS_DEPLOY_DRY_RUN": "false",
    }
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
    assert (result.returncode == 0) == (failure == "none")
    assert "测试凭据不得输出" not in result.stdout + result.stderr
    commands = calls.read_text() if calls.exists() else ""
    assert '"restart"' not in commands
    assert '"stop"' not in commands
    assert '"rm", "-f", "fusion-api"' not in commands
    if failure != "identity":
        assert '"rm", "-f", "baseline-by-test"' in commands
    if failure in {"capture", "identity"}:
        assert "created-by-test" not in commands
    else:
        assert '"network", "connect", "fusion-prompthub", "created-by-test"' in commands
        assert '"rm", "-f", "created-by-test"' in commands
