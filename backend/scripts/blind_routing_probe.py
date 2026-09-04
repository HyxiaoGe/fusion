#!/usr/bin/env python
"""能力路由盲测跑分。

用 `test/fixtures/blind_routing_probe.json` 里独立于实现书写的消息，跑真实的
`resolve_run_capability_route()`，报告按类别的覆盖率。默认调用真实 LiteLLM 混合
分类器；只有显式传入 `--classifier rules` 才使用规则基线。

**这不是 CI 门禁**：当前规则分类器在这份集合上远达不到 100%，把它接进 pytest 只会
让 CI 长期红着。它是诊断基准——换分类器实现（见 issue #24）后用同一套对比，才知道
是真的变好还是只是又拟合了一遍样本。

用法：

    DATABASE_URL="sqlite:///:memory:" python scripts/blind_routing_probe.py
    DATABASE_URL="sqlite:///:memory:" python scripts/blind_routing_probe.py --classifier rules --verbose
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.stream.agent_task_policy import AgentTaskPolicy  # noqa: E402
from app.services.stream.run_capability_model_classifier import (  # noqa: E402
    classify_capability_request_with_model,
)
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
TASK_POLICY = AgentTaskPolicy(
    task_mode="standard",
    plan_mode="auto",
    network_profile="standard",
    evidence_policy="standard",
)


def has_hybrid_classifier_credentials() -> bool:
    """盲测默认模式只在真实模型调用所需配置完整时运行。"""

    return bool(
        settings.LITELLM_API_KEY
        and settings.LITELLM_PROXY_URL
        and settings.RUN_CAPABILITY_CLASSIFIER_MODEL
        and not settings.RUN_CAPABILITY_CLASSIFIER_MODEL.startswith("litellm_proxy/")
    )


def route(message: str, *, classifier: CapabilityClassifier):
    return resolve_run_capability_route(
        original_message=message,
        task_context_messages=None,
        available_tool_names=AVAILABLE_TOOLS,
        requested_plan_mode="auto",
        task_policy=TASK_POLICY,
        capabilities={"functionCalling": True, "searchCapable": True},
        tools_disabled=False,
        knowledge_grounded=False,
        classify_fn=classifier,
    )


def _print_report(by_group: dict[str, list[bool]]) -> None:
    total_ok = sum(sum(results) for results in by_group.values())
    total_cases = sum(len(results) for results in by_group.values())
    print(f"{'类别':12} {'通过':>6} {'总数':>6} {'覆盖率':>8}")
    print("-" * 36)
    for group, results in sorted(by_group.items()):
        rate = sum(results) / len(results) * 100
        print(f"{group:12} {sum(results):>6} {len(results):>6} {rate:>7.0f}%")
    print("-" * 36)
    print(f"{'合计':12} {total_ok:>6} {total_cases:>6} {total_ok / total_cases * 100:>7.0f}%")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--classifier",
        choices=("hybrid", "rules"),
        default="hybrid",
        help="分类器：默认 hybrid 调用真实 LiteLLM；rules 仅用于显式回滚诊断",
    )
    parser.add_argument("--verbose", action="store_true", help="逐条打印明细")
    args = parser.parse_args(argv)

    if args.classifier == "hybrid" and not has_hybrid_classifier_credentials():
        print(
            "无法运行混合分类器盲测：缺少 LiteLLM 凭据。请配置 "
            "LITELLM_PROXY_URL、LITELLM_API_KEY 和 RUN_CAPABILITY_CLASSIFIER_MODEL；"
            "未调用模型，不能报告准确率。",
            file=sys.stderr,
        )
        return 2

    classifier: CapabilityClassifier = (
        classify_capability_request_with_model if args.classifier == "hybrid" else classify_capability_request
    )

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = payload["cases"]

    by_group: dict[str, list[bool]] = {}
    failures: list[tuple[str, str, str, list[str]]] = []

    for case in cases:
        resolution = route(case["question"], classifier=classifier)
        acceptable = case["acceptable_packages"]
        ok = resolution.package_id in acceptable
        by_group.setdefault(case["group"], []).append(ok)
        if not ok:
            failures.append((case["id"], case["question"], resolution.package_id, acceptable))
        if args.verbose:
            mark = "OK " if ok else "MISS"
            tools = ",".join(resolution.external_tool_names) or "-"
            print(f"{mark} {case['id']:14} {resolution.package_id:20} [{tools}]  {case['question']}")

    if args.verbose:
        print()

    _print_report(by_group)

    if failures and not args.verbose:
        print("\n未覆盖条目：")
        for case_id, question, actual, acceptable in failures:
            print(f"  {case_id:14} 实际={actual:20} 期望∈{acceptable}  {question}")

    # 诊断脚本，永远返回 0；覆盖率变化由人判断，不作为门禁。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
