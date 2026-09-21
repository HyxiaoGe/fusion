#!/usr/bin/env python3
"""动态工具发现对照入口：默认离线，dry-run 打印计划，live 需显式限额。

不因环境中存在密钥就联网，不打印凭据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_OUTPUT = _BACKEND_ROOT / "tmp" / "dynamic-tool-discovery"
CASES = [
    {
        "id": "greeting",
        "text": "早上好，你是谁？",
        "expect": "问候/身份，不必调用产品工具",
        "kind": "normal",
    },
    {
        "id": "weather_only",
        "text": "帮我看看杭州这周末天气怎么样",
        "expect": "单一天气查询",
        "kind": "normal",
    },
    {
        "id": "weather_and_train",
        "text": "查一下杭州周末天气，再找上海过去的高铁。",
        "expect": "天气加车次，候选路径应发现两类工具",
        "kind": "pressure_hidden_tools",
    },
    {
        "id": "tool_failure_fallback",
        "text": "先查杭州天气，要是天气接口不行就改用网页搜一下",
        "expect": "工具失败后改用授权目录内替代工具",
        "kind": "normal",
    },
    {
        "id": "no_network",
        "text": "本次不要联网，只用你已经知道的说杭州周末适不适合出门",
        "expect": "明确禁网，发现和执行都不能绕过",
        "kind": "normal",
    },
    {
        "id": "empty_then_specific",
        "text": "杭州到上海周六高铁有哪些，给我具体车次和票价",
        "expect": "空结果下索要具体班次，不得把合成空账本当已验证事实",
        "kind": "normal",
    },
]


def _git_sha(kind: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", kind],
            cwd=_BACKEND_ROOT.parent,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _print_plan(*, mode: str, repeats: int, max_requests: int | None, output_dir: Path) -> None:
    alias = os.environ.get("FUSION_COMPARE_MODEL_ALIAS", "fusion-main")
    print("dynamic_tool_discovery_compare")
    print(f"mode={mode}")
    print(f"model_alias={alias} (不输出凭据)")
    print(f"case_count={len(CASES)}")
    print(f"repeats_per_arm={repeats}")
    print("paths=baseline_package,candidate_discovery")
    print("limits_per_run: max_steps=8 max_tool_calls=12 max_tokens=unknown")
    print(f"experiment_request_cap={max_requests if max_requests is not None else 'unset'}")
    print(f"output_dir={output_dir}")
    print("cache_status=未知")
    print("timezone=Asia/Shanghai")
    print(f"fixed_date={datetime.now(_CHINA_TZ).date().isoformat()}")
    print("live 不会因环境变量中存在密钥自动启动")
    for case in CASES:
        print(f"case {case['id']}: {case['text']} | expect={case['expect']} | kind={case['kind']}")


def _case_hash(case: dict) -> str:
    payload = json.dumps({"id": case["id"], "text": case["text"]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def run_offline(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test/services/stream/test_dynamic_tool_discovery.py",
            "-q",
            "--tb=short",
        ],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    log_path = output_dir / "offline-pytest.log"
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    summary = {
        "mode": "offline",
        "exit_code": result.returncode,
        "base": _git_sha("HEAD"),
        "log": str(log_path),
        "cases": [{**case, "input_hash": _case_hash(case)} for case in CASES],
        "live_executed": False,
        "live_budget": None,
        "live_consumed": 0,
    }
    (output_dir / "offline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
    print(f"offline_log={log_path}")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="动态工具发现基线/候选对照")
    parser.add_argument("--mode", choices=("offline", "dry-run", "live"), default="offline")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    _print_plan(mode=args.mode, repeats=args.repeats, max_requests=args.max_requests, output_dir=args.output_dir)
    if args.mode == "dry-run":
        print("dry-run 完成：未调用模型、未消耗额度")
        return 0
    if args.mode == "live":
        if args.max_requests is None or args.max_requests <= 0:
            print("live 缺显式 --max-requests 限额，拒绝运行")
            return 2
        print("live 未执行：本轮任务未授权消耗真实模型预算")
        return 3
    return run_offline(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
