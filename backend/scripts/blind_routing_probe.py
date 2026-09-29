#!/usr/bin/env python
"""能力路由盲测跑分。

用 `test/fixtures/blind_routing_probe.json` 里独立于实现书写的消息，跑真实的
`resolve_run_capability_route()`，报告按类别的覆盖率。默认调用真实 LiteLLM 混合
分类器；只有显式传入 `--classifier rules` 才使用规则基线。

可选 expected_layer=literal/model 同时校验层归属；rules 中的 model 期望只验证
字面层委派，报告为 model_deferred（未调用模型），包成绩仍取规则基线。

每条可再声明 context（上一轮对话）、forbidden_tools / required_tools（本轮工具边界）、
critical（门禁必过）。--repeat N 每条跑 N 次，全部落在可接受包内才算通过，并统计一致率。

它要调真实分类模型，所以不进 pytest；--gate 由 Fusion dev deploy 在部署后于 dev 容器内
运行（改动分类器相关文件时），阈值取夹具的 gate 配置，不达标返回 1。分类失败比例超过
gate.max_classifier_failure_rate 时视为上游故障，返回 3 且不输出准确率。

用法：

    DATABASE_URL="sqlite:///:memory:" python scripts/blind_routing_probe.py
    DATABASE_URL="sqlite:///:memory:" python scripts/blind_routing_probe.py --classifier rules --verbose
    python scripts/blind_routing_probe.py --gate --workers 4
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.stream.agent_task_policy import AgentTaskPolicy  # noqa: E402
from app.services.stream.run_capability_router import (  # noqa: E402
    CapabilityClassifier,
    classify_capability_request,
    resolve_run_capability_route,
)

FIXTURE = _BACKEND_ROOT / "test" / "fixtures" / "blind_routing_probe.json"

AVAILABLE_TOOLS = [
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
]
# 夹具未声明 gate 时的兜底阈值；实际门槛以夹具为准。
_DEFAULT_GATE = {"min_pass_rate": 0.9, "min_consistency": 0.9, "max_classifier_failure_rate": 0.05, "repeat": 2}
TASK_POLICY = AgentTaskPolicy(
    task_mode="standard",
    plan_mode="auto",
    network_profile="standard",
    evidence_policy="standard",
)


def has_hybrid_classifier_credentials() -> bool:
    """盲测默认模式只在真实模型调用所需配置完整时运行。"""

    api_key = settings.LITELLM_API_KEY.strip()
    proxy_url = settings.LITELLM_PROXY_URL.strip()
    raw_model_alias = settings.RUN_CAPABILITY_CLASSIFIER_MODEL
    model_alias = raw_model_alias.strip()
    parsed_proxy_url = urlparse(proxy_url)
    return bool(
        api_key
        and parsed_proxy_url.scheme in {"http", "https"}
        and parsed_proxy_url.netloc
        and model_alias
        and raw_model_alias == model_alias
        and not model_alias.startswith("litellm_proxy/")
    )


class HybridClassifierMonitor:
    """为盲测记录低基数分类结果，不改变 Router 的候选包协议。"""

    def __init__(self, classifier):
        self._classifier = classifier
        self.failure_error_type: str | None = None
        self.layer: str | None = None

    def __call__(self, *, message, task_context_messages, available_tool_names):
        self.layer = None
        return self._classifier(
            message=message,
            task_context_messages=task_context_messages,
            available_tool_names=available_tool_names,
            result_callback=self._record_result,
        )

    def _record_result(self, result: str, error_type: str | None) -> None:
        self.layer = result
        if result == "failed" and self.failure_error_type is None:
            self.failure_error_type = error_type or "unknown"


class RulesClassifierMonitor:
    """字面层已删除（#132）：规则基线现在只剩 fail-closed 落点，不做语义预选。

    保留这个入口是为了让 --classifier rules 仍可运行并显示「无模型时会发生什么」，
    它不再是一条可用于评分的基线。
    """

    def __init__(self):
        self.layer: str | None = None

    def __call__(self, *, message, task_context_messages, available_tool_names):
        self.layer = "model_deferred"
        return classify_capability_request(
            message=message,
            task_context_messages=task_context_messages,
            available_tool_names=available_tool_names,
        )


def _new_hybrid_classifier_monitor() -> HybridClassifierMonitor:
    from app.services.stream.run_capability_model_classifier import classify_capability_request_with_model

    return HybridClassifierMonitor(classify_capability_request_with_model)


def route(
    message: str,
    *,
    classifier: CapabilityClassifier,
    available_tools: Sequence[str] | None = None,
    context: Sequence[dict] | None = None,
):
    return resolve_run_capability_route(
        original_message=message,
        task_context_messages=list(context) if context else None,
        available_tool_names=AVAILABLE_TOOLS if available_tools is None else available_tools,
        requested_plan_mode="auto",
        task_policy=TASK_POLICY,
        capabilities={"functionCalling": True, "searchCapable": True},
        tools_disabled=False,
        knowledge_grounded=False,
        classify_fn=classifier,
    )


@dataclass(frozen=True)
class _Attempt:
    package_id: str
    tools: tuple[str, ...]
    ok: bool
    detail: str
    error_type: str | None


@dataclass(frozen=True)
class _CaseResult:
    case: dict
    attempts: tuple[_Attempt, ...]

    @property
    def ok(self) -> bool:
        return all(attempt.ok for attempt in self.attempts)

    @property
    def consistent(self) -> bool:
        return len({attempt.package_id for attempt in self.attempts}) == 1


def _run_attempt(case: dict, args: argparse.Namespace, shared_classifier: CapabilityClassifier | None) -> _Attempt:
    expected_layer = case.get("expected_layer")
    hybrid_monitor = _new_hybrid_classifier_monitor() if args.classifier == "hybrid" else None
    rules_monitor = RulesClassifierMonitor() if args.classifier == "rules" and expected_layer is not None else None
    classifier = hybrid_monitor or rules_monitor or shared_classifier
    resolution = route(
        case["question"],
        classifier=classifier,
        available_tools=case.get("available_tools"),
        context=case.get("context"),
    )
    tools = tuple(resolution.external_tool_names)
    error_type = hybrid_monitor.failure_error_type if hybrid_monitor is not None else None
    ok = error_type is None and resolution.package_id in case["acceptable_packages"]
    details: list[str] = []
    forbidden = sorted(set(case.get("forbidden_tools", ())) & set(tools))
    if forbidden:
        ok = False
        details.append(f" 不应开放={forbidden}")
    missing = sorted(set(case.get("required_tools", ())) - set(tools))
    if missing:
        ok = False
        details.append(f" 缺少工具={missing}")
    if expected_layer is not None:
        layer = (rules_monitor or hybrid_monitor).layer
        layer_ok = layer == expected_layer or (expected_layer == "model" and layer == "model_deferred")
        layer_detail = (
            f" layer={layer or 'unknown'} expected_layer={expected_layer} layer_check={'OK' if layer_ok else 'MISS'}"
        )
        if layer == "model_deferred":
            layer_detail += "（仅字面委派，未调用模型）"
        details.append(layer_detail)
        ok = ok and layer_ok
    if error_type is not None:
        details.append(f" 分类失败={error_type}")
    return _Attempt(resolution.package_id, tools, ok, "".join(details), error_type)


def _run_cases(cases: list[dict], args: argparse.Namespace, repeat: int) -> list[_CaseResult]:
    shared_classifier = classify_capability_request if args.classifier == "rules" else None
    jobs = [(index, case) for index, case in enumerate(cases) for _ in range(repeat)]
    attempts: dict[int, list[_Attempt]] = {index: [] for index in range(len(cases))}
    if args.workers <= 1:
        for index, case in jobs:
            attempts[index].append(_run_attempt(case, args, shared_classifier))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [(index, pool.submit(_run_attempt, case, args, shared_classifier)) for index, case in jobs]
            for index, future in futures:
                attempts[index].append(future.result())
    return [_CaseResult(case, tuple(attempts[index])) for index, case in enumerate(cases)]


def _print_report(results: list[_CaseResult]) -> None:
    by_group: dict[str, list[_CaseResult]] = {}
    for result in results:
        by_group.setdefault(result.case["group"], []).append(result)
    print(f"{'类别':12} {'通过':>6} {'总数':>6} {'覆盖率':>8} {'一致率':>8}")
    print("-" * 46)
    for group, group_results in sorted(by_group.items()):
        passed = sum(result.ok for result in group_results)
        consistent = sum(result.consistent for result in group_results)
        total = len(group_results)
        print(f"{group:12} {passed:>6} {total:>6} {passed / total * 100:>7.0f}% {consistent / total * 100:>7.0f}%")
    print("-" * 46)
    passed = sum(result.ok for result in results)
    consistent = sum(result.consistent for result in results)
    total = len(results)
    print(f"{'合计':12} {passed:>6} {total:>6} {passed / total * 100:>7.0f}% {consistent / total * 100:>7.0f}%")


def _print_misses(results: list[_CaseResult]) -> None:
    misses = [result for result in results if not result.ok]
    if not misses:
        return
    print("\n未覆盖条目：")
    for result in misses:
        case = result.case
        seen = ",".join(attempt.package_id for attempt in result.attempts)
        detail = next((attempt.detail for attempt in result.attempts if not attempt.ok), "")
        print(f"  {case['id']:14} 实际={seen:20} 期望∈{case['acceptable_packages']}  {case['question']}{detail}")
    destinations = Counter(
        attempt.package_id
        for result in misses
        for attempt in result.attempts
        if attempt.package_id not in result.case["acceptable_packages"]
    )
    if destinations:
        print("误判去向：" + "，".join(f"{package}×{count}" for package, count in destinations.most_common()))


def _gate_failures(results: list[_CaseResult], gate: dict) -> list[str]:
    total = len(results)
    pass_rate = sum(result.ok for result in results) / total
    consistency = sum(result.consistent for result in results) / total
    reasons = []
    if pass_rate < gate["min_pass_rate"]:
        reasons.append(f"通过率 {pass_rate:.1%} 低于门槛 {gate['min_pass_rate']:.0%}")
    if consistency < gate["min_consistency"]:
        reasons.append(f"一致率 {consistency:.1%} 低于门槛 {gate['min_consistency']:.0%}")
    critical = [result.case["id"] for result in results if result.case.get("critical") and not result.ok]
    if critical:
        reasons.append(f"关键条目未通过：{', '.join(critical)}")
    return reasons


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--classifier",
        choices=("hybrid", "rules"),
        default="hybrid",
        help="分类器：默认 hybrid 调用真实 LiteLLM；rules 仅用于显式回滚诊断",
    )
    parser.add_argument("--verbose", action="store_true", help="逐条打印明细")
    parser.add_argument("--gate", action="store_true", help="按夹具 gate 阈值判定，不达标返回 1")
    parser.add_argument("--repeat", type=int, default=None, help="每条重复次数；--gate 时默认取夹具配置")
    parser.add_argument("--workers", type=int, default=1, help="并发调用数")
    args = parser.parse_args(argv)

    if args.classifier == "hybrid" and not has_hybrid_classifier_credentials():
        print(
            "无法运行混合分类器盲测：缺少 LiteLLM 凭据。请配置 "
            "LITELLM_PROXY_URL、LITELLM_API_KEY 和 RUN_CAPABILITY_CLASSIFIER_MODEL；"
            "未调用模型，不能报告准确率。",
            file=sys.stderr,
        )
        return 2

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    gate = {**_DEFAULT_GATE, **payload.get("gate", {})}
    repeat = args.repeat or (gate["repeat"] if args.gate else 1)
    results = _run_cases(payload["cases"], args, max(1, repeat))

    attempts = [attempt for result in results for attempt in result.attempts]
    failed = Counter(attempt.error_type for attempt in attempts if attempt.error_type is not None)
    failure_rate = sum(failed.values()) / len(attempts)
    if failure_rate > gate["max_classifier_failure_rate"]:
        # 分类失败多到这个程度是上游或配置问题，此时的准确率没有意义，不输出。
        summary = "，".join(f"{error}×{count}" for error, count in failed.most_common())
        print(
            f"无法完成混合分类器盲测：模型分类失败 {sum(failed.values())}/{len(attempts)} 次（{summary}）；未输出准确率。",
            file=sys.stderr,
        )
        return 3

    if args.verbose:
        for result in results:
            for attempt in result.attempts:
                mark = "OK " if attempt.ok else "MISS"
                tools = ",".join(attempt.tools) or "-"
                print(
                    f"{mark} {result.case['id']:14} {attempt.package_id:20} [{tools}]  "
                    f"{result.case['question']}{attempt.detail}"
                )
        print()

    _print_report(results)
    if failed:
        print("分类失败：" + "，".join(f"{error}×{count}" for error, count in failed.most_common()))
    if not args.verbose:
        _print_misses(results)

    if not args.gate:
        # 诊断模式永远返回 0；覆盖率变化由人判断。
        return 0
    reasons = _gate_failures(results, gate)
    if reasons:
        print("\n门禁未通过：" + "；".join(reasons), file=sys.stderr)
        return 1
    print(f"\n门禁通过（每条 {repeat} 次）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
