#!/usr/bin/env python
"""回放评测：把 evals/cases 里的用例在指定模型上真实跑一遍，按轨迹判分并入库。

默认只列出将要运行的 用例 × 模型，不调用任何模型；--apply 才真正发送（会产生模型费用）。
必须在 fusion-api 容器内运行（需要数据库、Redis 与代理配置）：

    docker exec -w /app fusion-api python -m scripts.run_eval_suite --models qwen3.8-flash
    docker exec -w /app fusion-api python -m scripts.run_eval_suite --models qwen3.8-flash,mimo-v2.6-pro \\
        --category escalation --apply
    docker exec -w /app fusion-api python -m scripts.run_eval_suite --list

退出码：0 完成且无退步；1 存在退步（仅 --fail-on-regression）；2 参数或用例错误；3 运行中止。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.evals.cases import EvalCase, load_suite  # noqa: E402


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回放评测")
    parser.add_argument("--models", type=_csv, default=[], help="逗号分隔的模型 id")
    parser.add_argument("--case", action="append", default=None, help="只跑指定用例 id，可重复")
    parser.add_argument("--category", action="append", default=None, help="只跑指定类别，可重复")
    parser.add_argument("--label", default="manual", help="本轮评测标签，如 manual / deploy / pr-270")
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--workers", type=int, default=2, help="并发用例数")
    parser.add_argument("--no-judge", action="store_true", help="跳过裁判模型检查（记为 error）")
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--list", action="store_true", help="列出全部用例后退出")
    parser.add_argument("--apply", action="store_true", help="真正发送并判分（产生模型费用）")
    return parser


def plan(cases: Sequence[EvalCase], models: Sequence[str]) -> list[tuple[EvalCase, str]]:
    return [(case, model) for case in cases for model in models if case.applies_to(model)]


def format_report(outcomes, regressions, fixes) -> str:
    from app.evals.store import summarize

    lines = []
    for outcome in sorted(outcomes, key=lambda item: (item.case_id, item.model_id)):
        if outcome.status == "passed":
            continue
        lines.append(f"[{outcome.status}] {outcome.case_id} @ {outcome.model_id}  run={outcome.run_id}")
        if outcome.error:
            lines.append(f"    {outcome.error}")
        for check in outcome.checks:
            if check.status != "passed":
                lines.append(f"    - {check.type}: {check.detail}")
    lines.append("")
    lines.append(f"{'模型':<32}{'通过':>6}{'失败':>6}{'错误':>6}{'合计':>6}")
    for model, counts in sorted(summarize(outcomes).items()):
        lines.append(f"{model:<32}{counts['passed']:>6}{counts['failed']:>6}{counts['error']:>6}{counts['total']:>6}")
    if regressions:
        lines.append("")
        lines.append(f"退步 {len(regressions)} 项（此前通过、本轮失败）：")
        lines.extend(f"  - {item.case_id} @ {item.model_id}（基准 {item.before_suite_run_id}）" for item in regressions)
    if fixes:
        lines.append("")
        lines.append(f"改善 {len(fixes)} 项（此前失败、本轮通过）：")
        lines.extend(f"  - {item.case_id} @ {item.model_id}" for item in fixes)
    return "\n".join(lines)


async def _execute(args, cases: list[EvalCase], pairs: list[tuple[EvalCase, str]], suite_sha256: str) -> int:
    from app.db.database import SessionLocal
    from app.evals import store
    from app.evals.judge import judge_answer
    from app.evals.runner import ensure_eval_user, eval_runtime, run_case
    from app.utils.time import utc_now

    started: datetime = utc_now()
    with SessionLocal() as db:
        user_id = ensure_eval_user(db)
        suite_run_id = store.start_suite_run(
            db,
            label=args.label,
            git_sha=args.git_sha,
            suite_sha256=suite_sha256,
            models=args.models,
            case_ids=[case.id for case in cases],
        )
    print(f"评测 {suite_run_id}：{len(pairs)} 个 用例×模型，并发 {args.workers}", flush=True)

    judge = None if args.no_judge else judge_answer
    semaphore = asyncio.Semaphore(max(1, args.workers))
    outcomes = []

    async def one(case: EvalCase, model: str) -> None:
        async with semaphore:
            outcome = await run_case(case, model, user_id=user_id, session_factory=SessionLocal, judge=judge)
        with SessionLocal() as db:
            store.save_outcome(db, suite_run_id, outcome)
        outcomes.append(outcome)
        print(f"  [{outcome.status}] {case.id} @ {model}  {outcome.duration_ms / 1000:.0f}s", flush=True)

    try:
        async with eval_runtime():
            await asyncio.gather(*(one(case, model) for case, model in pairs))
    except BaseException:
        with SessionLocal() as db:
            store.finish_suite_run(db, suite_run_id, status="failed", summary=store.summarize(outcomes))
        raise

    with SessionLocal() as db:
        summary = store.summarize(outcomes)
        store.finish_suite_run(db, suite_run_id, status="completed", summary=summary)
        previous = store.previous_results(
            db,
            before=started,
            pairs={(item.case_id, item.model_id) for item in outcomes},
            exclude_suite_run_id=suite_run_id,
        )
    regressions, fixes = store.compare(outcomes, previous)
    print()
    print(format_report(outcomes, regressions, fixes))
    return 1 if regressions and args.fail_on_regression else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        suite = load_suite()
        cases = suite.select(
            case_ids=set(args.case) if args.case else None,
            categories=set(args.category) if args.category else None,
        )
    except ValueError as exc:
        print(f"用例错误: {exc}", file=sys.stderr)
        return 2
    if args.list:
        for case in suite.cases:
            scope = ",".join(case.models) if case.models else "全部模型"
            print(f"{case.category:<14}{case.id:<48}{scope}")
        print(f"\n共 {len(suite.cases)} 条，suite sha256={suite.sha256[:12]}")
        return 0
    if not args.models:
        print("需要 --models", file=sys.stderr)
        return 2
    pairs = plan(cases, args.models)
    if not pairs:
        print("没有可运行的 用例×模型", file=sys.stderr)
        return 2
    if not args.apply:
        for case, model in pairs:
            print(f"{case.id} @ {model}")
        print(f"\n共 {len(pairs)} 个 用例×模型；加 --apply 真正运行（会产生模型费用）")
        return 0
    try:
        return asyncio.run(_execute(args, cases, pairs, suite.sha256))
    except KeyboardInterrupt:
        return 3


if __name__ == "__main__":
    sys.exit(main())
