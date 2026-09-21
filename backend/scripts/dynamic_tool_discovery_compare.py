#!/usr/bin/env python3
"""动态工具发现对照入口：默认离线，dry-run 打印实际配置，live 需显式限额。

不因环境中存在密钥就联网，不打印凭据。真实模型传输层本轮不连接；
配对执行器用可注入的假传输层验证调度、预算与落盘。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

EXPERIMENT_TZ = timezone(timedelta(hours=8))
EXPERIMENT_NOW = datetime(2026, 9, 22, 9, 0, tzinfo=EXPERIMENT_TZ)

DEFAULT_OUTPUT = _BACKEND_ROOT / "tmp" / "dynamic-tool-discovery"
LIMITS_PER_RUN = {
    "max_steps": 8,
    "max_tool_calls": 12,
    "max_tokens": 4096,
}
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


class ModelTransport(Protocol):
    def complete(self, request: "ModelRequest") -> "ModelResponse": ...


@dataclass(frozen=True)
class ModelRequest:
    arm: str
    case_id: str
    repeat: int
    phase: str
    messages: list[dict[str, Any]]
    tools: list[str]
    tool_choice: Any
    classify: bool = False


@dataclass(frozen=True)
class ModelResponse:
    status: str
    request_hash: str
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class ExperimentBudget:
    max_requests: int
    max_tokens: int = LIMITS_PER_RUN["max_tokens"]
    used_requests: int = 0
    used_tokens: int = 0
    aborted: bool = False

    def consume(self, *, requests: int = 1, tokens: int = 0) -> bool:
        if self.aborted:
            return False
        if self.used_requests + requests > self.max_requests:
            self.aborted = True
            return False
        if self.max_tokens and self.used_tokens + tokens > self.max_tokens:
            self.aborted = True
            return False
        self.used_requests += requests
        self.used_tokens += tokens
        return True


def request_hash(request: ModelRequest) -> str:
    payload = {
        "arm": request.arm,
        "case_id": request.case_id,
        "repeat": request.repeat,
        "phase": request.phase,
        "messages": request.messages,
        "tools": request.tools,
        "tool_choice": request.tool_choice,
        "classify": request.classify,
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class FakeModelTransport:
    """假传输层：扣预算、记请求 hash，不连接真实模型。"""

    def __init__(self, budget: ExperimentBudget) -> None:
        self.budget = budget
        self.calls: list[dict[str, Any]] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        digest = request_hash(request)
        tokens = 8 if request.classify else 16
        if not self.budget.consume(requests=1, tokens=tokens):
            response = ModelResponse(status="budget_aborted", request_hash=digest, error="experiment_request_cap")
            self.calls.append({"request": asdict(request), "response": asdict(response)})
            return response
        if request.classify:
            content = json.dumps({"package_id": "direct", "arm": "baseline"}, ensure_ascii=False)
        elif request.arm == "candidate":
            content = f"candidate:{request.case_id}:discovery"
        else:
            content = f"baseline:{request.case_id}:package"
        response = ModelResponse(
            status="ok",
            request_hash=digest,
            content=content,
            input_tokens=tokens // 2,
            output_tokens=tokens - tokens // 2,
        )
        self.calls.append({"request": asdict(request), "response": asdict(response)})
        return response


class PairingExecutor:
    def __init__(
        self,
        *,
        transport: FakeModelTransport,
        budget: ExperimentBudget,
        output_dir: Path,
        cases: list[dict[str, Any]] | None = None,
        repeats: int = 2,
    ) -> None:
        self.transport = transport
        self.budget = budget
        self.output_dir = output_dir
        self.cases = cases or CASES
        self.repeats = repeats

    def jobs(self) -> list[tuple[str, dict[str, Any], int]]:
        ordered: list[tuple[str, dict[str, Any], int]] = []
        for repeat in range(self.repeats):
            for case in self.cases:
                ordered.append(("baseline", case, repeat))
                ordered.append(("candidate", case, repeat))
        return ordered

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        completed: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        for arm, case, repeat in self.jobs():
            if self.budget.aborted:
                incomplete.append({"arm": arm, "case_id": case["id"], "repeat": repeat, "reason": "budget_aborted"})
                continue
            record = self._run_arm(arm=arm, case=case, repeat=repeat)
            path = self.output_dir / f"{arm}-{case['id']}-r{repeat}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            record["output_path"] = str(path)
            if record.get("status") == "ok":
                completed.append(record)
            else:
                incomplete.append(record)
        summary = {
            "mode": "live-fake-transport",
            "fixed_date": EXPERIMENT_NOW.date().isoformat(),
            "timezone": "Asia/Shanghai",
            "limits_per_run": LIMITS_PER_RUN,
            "max_requests": self.budget.max_requests,
            "used_requests": self.budget.used_requests,
            "used_tokens": self.budget.used_tokens,
            "aborted": self.budget.aborted,
            "completed": completed,
            "incomplete": incomplete,
            "job_order": [f"{arm}:{case['id']}:r{repeat}" for arm, case, repeat in self.jobs()],
            "base": _git_sha("HEAD"),
            "live_executed": False,
            "live_real_model": False,
            "cache_status": "未知",
        }
        (self.output_dir / "pairing-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return summary

    def _run_arm(self, *, arm: str, case: dict[str, Any], repeat: int) -> dict[str, Any]:
        phases: list[dict[str, Any]] = []
        if arm == "baseline":
            classify_request = ModelRequest(
                arm=arm,
                case_id=case["id"],
                repeat=repeat,
                phase="classify",
                messages=[{"role": "user", "content": case["text"]}],
                tools=[],
                tool_choice=None,
                classify=True,
            )
            classify_response = self.transport.complete(classify_request)
            phases.append({"phase": "classify", **asdict(classify_response)})
            if classify_response.status != "ok":
                return {
                    "status": classify_response.status,
                    "arm": arm,
                    "case_id": case["id"],
                    "repeat": repeat,
                    "input_hash": _case_hash(case),
                    "phases": phases,
                    "final_output": None,
                }
        main_request = ModelRequest(
            arm=arm,
            case_id=case["id"],
            repeat=repeat,
            phase="main",
            messages=[{"role": "user", "content": case["text"]}],
            tools=["tool_search"] if arm == "candidate" else ["web_search"],
            tool_choice="auto",
        )
        main_response = self.transport.complete(main_request)
        phases.append({"phase": "main", **asdict(main_response)})
        return {
            "status": main_response.status,
            "arm": arm,
            "case_id": case["id"],
            "repeat": repeat,
            "input_hash": _case_hash(case),
            "request_hash": main_response.request_hash,
            "phases": phases,
            "final_output": main_response.content,
            "fixed_date": EXPERIMENT_NOW.isoformat(),
            "fixture_clock": EXPERIMENT_NOW.isoformat(),
        }


def build_compare_config(*, mode: str, repeats: int, max_requests: int | None, output_dir: Path) -> dict[str, Any]:
    alias = os.environ.get("FUSION_COMPARE_MODEL_ALIAS", "fusion-main")
    return {
        "mode": mode,
        "model_alias": alias,
        "case_count": len(CASES),
        "repeats_per_arm": repeats,
        "paths": ["baseline_package", "candidate_discovery"],
        "limits_per_run": LIMITS_PER_RUN,
        "experiment_request_cap": max_requests,
        "output_dir": str(output_dir),
        "cache_status": "未知",
        "timezone": "Asia/Shanghai",
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
        "cases": [{**case, "input_hash": _case_hash(case)} for case in CASES],
        "live_will_not_start_from_env_secrets": True,
    }


def _git_sha(kind: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", kind],
            cwd=_BACKEND_ROOT.parent,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _print_plan(config: dict[str, Any]) -> None:
    print("dynamic_tool_discovery_compare")
    print(f"mode={config['mode']}")
    print(f"model_alias={config['model_alias']} (不输出凭据)")
    print(f"case_count={config['case_count']}")
    print(f"repeats_per_arm={config['repeats_per_arm']}")
    print("paths=baseline_package,candidate_discovery")
    limits = config["limits_per_run"]
    print(
        "limits_per_run: "
        f"max_steps={limits['max_steps']} max_tool_calls={limits['max_tool_calls']} max_tokens={limits['max_tokens']}"
    )
    cap = config["experiment_request_cap"]
    print(f"experiment_request_cap={cap if cap is not None else 'unset'}")
    print(f"output_dir={config['output_dir']}")
    print(f"cache_status={config['cache_status']}")
    print(f"timezone={config['timezone']}")
    print(f"fixed_date={config['fixed_date']}")
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
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
    }
    (output_dir / "offline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
    print(f"offline_log={log_path}")
    return result.returncode


def run_fake_live(*, max_requests: int, repeats: int, output_dir: Path) -> int:
    budget = ExperimentBudget(max_requests=max_requests)
    transport = FakeModelTransport(budget)
    summary = PairingExecutor(
        transport=transport,
        budget=budget,
        output_dir=output_dir / "pairing",
        repeats=repeats,
    ).run()
    print(json.dumps({"used_requests": summary["used_requests"], "aborted": summary["aborted"]}, ensure_ascii=False))
    print(f"pairing_summary={output_dir / 'pairing' / 'pairing-summary.json'}")
    return 0


def parse_budget(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value <= 0:
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="动态工具发现基线/候选对照")
    parser.add_argument("--mode", choices=("offline", "dry-run", "live"), default="offline")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--transport", choices=("fake", "litellm"), default="fake")
    args = parser.parse_args(argv)
    config = build_compare_config(
        mode=args.mode,
        repeats=args.repeats,
        max_requests=args.max_requests,
        output_dir=args.output_dir,
    )
    _print_plan(config)
    if args.mode == "dry-run":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "dry-run-config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("dry-run 完成：未调用模型、未消耗额度；以上来自实际对照配置")
        return 0
    if args.mode == "live":
        budget = parse_budget(args.max_requests)
        if budget is None:
            print("live 缺显式 --max-requests 限额，拒绝运行")
            return 2
        if args.transport != "fake":
            print("live 未执行：本轮未授权连接真实模型传输层")
            return 3
        return run_fake_live(max_requests=budget, repeats=args.repeats, output_dir=args.output_dir)
    return run_offline(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
