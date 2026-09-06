"""只由 fusion-dev 串行 workflow 调用；默认只核验，不改变迁移阶段。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ENV_KEYS = (
    "DATABASE_URL",
    "PROMPTHUB_PROJECT_SLUG",
    "PROMPTHUB_SYNC_MODE",
    "PROMPT_P0_BASELINE_ATTESTED",
)
IMAGE = re.compile(
    r"crpi-77w10wlykpqilmmb\.cn-shenzhen\.personal\.cr\.aliyuncs\.com/seanfield/fusion-api@sha256:[0-9a-f]{64}"
)


def docker(*args, input=None, env=None):
    result = subprocess.run(
        ["docker", *args],
        input=input,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode:
        # Docker 错误可能携带连接串；不把原始 stderr 转成发布日志。
        raise RuntimeError("Docker 引擎迁移检查失败；保留原阶段，需在受控环境排查")
    return result.stdout


def inventory():
    ids = docker(
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.service=fusion-api",
        "--format",
        "{{.ID}}",
    ).split()
    if not ids:
        raise ValueError("未发现受管 API worker")
    rows = json.loads(docker("inspect", *ids))
    if any(
        row["State"]["Status"] != "running"
        and row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "no")
        not in {"no", ""}
        for row in rows
    ):
        raise ValueError("存在可自动恢复的停止/异常受管容器，不能证明旧 reader 已退出")
    current = [
        row
        for row in rows
        if row["State"]["Status"] not in {"exited", "dead", "created"}
    ]
    if not current or not any(row["Name"] == "/fusion-api" for row in current):
        raise ValueError("缺少当前 fusion-api worker")
    for row in current:
        if row["State"]["Status"] != "running" or not IMAGE.fullmatch(
            row["Config"]["Image"]
        ):
            raise ValueError("所有受管 worker 必须稳定运行且绑定 API repository digest")
        if row["State"].get("Health", {}).get("Status", "healthy") != "healthy":
            raise ValueError("当前 worker 容器健康检查未通过")
        docker(
            "exec",
            row["Id"],
            "python",
            "-c",
            "import json, urllib.request; response=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); ok=response.status == 200 and json.load(response)['status'] == 'healthy'; raise SystemExit(0 if ok else 1)",
        )
        image = json.loads(docker("image", "inspect", row["Config"]["Image"]))[0]
        if image["Id"] != row["Image"]:
            raise ValueError("worker image ID 与不可变 ref 不一致")
    return sorted(current, key=lambda row: row["Id"])


def config(row):
    result = {}
    for item in row["Config"].get("Env") or []:
        key, sep, value = item.partition("=")
        if sep and key in ENV_KEYS:
            if key in result:
                raise ValueError("worker Prompt 配置重复")
            result[key] = value
    if not result.get("DATABASE_URL"):
        raise ValueError("worker 缺少数据库配置")
    return result


def identity(rows):
    return [
        (row["Id"], row["Image"], row["Config"]["Image"], config(row)) for row in rows
    ]


def probe_source(stage):
    source = (
        Path(__file__).with_name("prompt-hold-preflight.py").read_text(encoding="utf-8")
    )
    required = ["none", "jinja2"] if stage == "bridge" else ["jinja2"]
    return (
        source
        + "\n"
        + f"""
from app.core.config import settings
if not settings.PROMPT_P0_BASELINE_ATTESTED:
    raise ValueError('目标迁移阶段要求 P0 attestation')
verify_engine_reader_profiles({required!r})
print('PROMPT_ENGINE_PROOF=' + json.dumps({{**prompt_hold_preflight_result, 'project_slug': settings.PROMPTHUB_PROJECT_SLUG}}, ensure_ascii=False))
"""
    )


def proof(output, expected_revision):
    lines = [
        line.removeprefix("PROMPT_ENGINE_PROOF=")
        for line in output.splitlines()
        if line.startswith("PROMPT_ENGINE_PROOF=")
    ]
    if len(lines) != 1:
        raise ValueError("目标没有返回唯一真实冻结证据")
    result = json.loads(lines[0])
    if (
        result.get("source_kind") != "prompthub_lkg"
        or result.get("revision") != expected_revision
    ):
        raise ValueError("消费者的完整冻结身份与待提升基线不一致")
    return result


def transition(args):
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REF") != "refs/heads/master"
    ):
        raise ValueError("只能在 master 的 fusion-dev 串行发布协调范围内执行")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", args.expected_revision)
        or not args.reason.strip()
    ):
        raise ValueError("必须给出明确 revision 和原因")
    rows = inventory()
    primary = next(row for row in rows if row["Name"] == "/fusion-api")
    source = probe_source(args.stage)
    results = [
        proof(
            docker("exec", "-i", row["Id"], "python", "-", input=source),
            args.expected_revision,
        )
        for row in rows
    ]
    if len({result["project_slug"] for result in results}) != 1 or any(
        config(row) != config(primary) for row in rows
    ):
        raise ValueError("同一受管 worker 集的项目或 Prompt 配置不一致")
    # 当前健康 bridge 镜像成为明确的新回滚锚点，不复用上次发布留下的旧引擎镜像。
    environment = {**os.environ, **config(primary)}
    command = ["run", "--rm", "-i", "--network", "postgres_default"]
    for key in config(primary):
        command.extend(["-e", key])
    command.extend(["--entrypoint", "python", primary["Image"], "-"])
    proof(docker(*command, input=source, env=environment), args.expected_revision)
    if identity(inventory()) != identity(rows):
        raise ValueError("核验期间 worker 身份或配置发生变化，拒绝提升")
    evidence = {
        "coordination": "fusion-dev",
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "workers": [row["Id"] for row in rows],
        "worker_image_ids": [row["Image"] for row in rows],
        "rollback_image": primary["Config"]["Image"],
        "rollback_image_id": primary["Image"],
        "probe_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }
    if not args.apply:
        return {
            "status": "validated",
            "mode": "dry-run",
            "stage": args.stage,
            "evidence": evidence,
        }
    return apply_transition(args, primary, evidence)


def apply_transition(args, primary, evidence):
    request = {
        "stage": args.stage,
        "expected_revision": args.expected_revision,
        "actor": os.environ["GITHUB_ACTOR"],
        "reason": args.reason,
        "evidence": evidence,
    }
    source = "\n".join(
        [
            "import json",
            "from app.services.prompt_engine_policy import advance_prompt_engine_policy",
            "request = json.loads("
            + repr(json.dumps(request, ensure_ascii=False))
            + ")",
            "print(json.dumps(advance_prompt_engine_policy(**request), ensure_ascii=False))",
        ]
    )
    output = docker("exec", "-i", primary["Id"], "python", "-", input=source)
    return {
        "status": "applied",
        "stage": args.stage,
        "result": json.loads(output),
        "evidence": evidence,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="完整 Prompt 引擎迁移阶段提升")
    parser.add_argument("--stage", choices=["bridge", "jinja2"], required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(transition(parser.parse_args(argv)), ensure_ascii=False))


if __name__ == "__main__":
    main()
