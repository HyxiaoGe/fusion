"""评测结果入库与前后对比。

对比基准按"用例 × 模型"取此前已完成评测里最近一次结果（有复核时取复核结果），
每轮选的用例或模型不同也能比较。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import EvalCaseResult, EvalSuiteRun
from app.evals.runner import CaseOutcome
from app.evals.usage import TokenUsage
from app.utils.time import utc_now


@dataclass(frozen=True)
class Change:
    case_id: str
    model_id: str
    before: str
    after: str
    before_suite_run_id: str


def start_suite_run(
    db: Session, *, label: str, git_sha: str | None, suite_sha256: str, models: list[str], case_ids: list[str]
) -> str:
    run = EvalSuiteRun(
        label=label,
        git_sha=git_sha,
        suite_sha256=suite_sha256,
        models=models,
        case_ids=case_ids,
        status="running",
        summary={},
    )
    db.add(run)
    db.commit()
    return run.id


def save_outcome(db: Session, suite_run_id: str, outcome: CaseOutcome) -> None:
    db.add(
        EvalCaseResult(
            suite_run_id=suite_run_id,
            case_id=outcome.case_id,
            model_id=outcome.model_id,
            status=outcome.status,
            checks=[item.as_dict() for item in outcome.checks],
            trajectory=outcome.snapshot,
            error=outcome.error,
            run_id=outcome.run_id,
            conversation_id=outcome.conversation_id,
            duration_ms=outcome.duration_ms,
            attempt=outcome.attempt,
            usage={
                "model": outcome.usage.as_dict() if outcome.usage is not None else None,
                "judge": outcome.judge_usage.as_dict(),
            },
        )
    )
    db.commit()


def summarize(outcomes: list[CaseOutcome]) -> dict[str, dict[str, Any]]:
    """判分按每个 (用例, 模型) 的最终结果计；花费按全部运行计，复核也花了钱。"""
    summary: dict[str, Counter] = {}
    for outcome in final_outcomes(outcomes):
        summary.setdefault(outcome.model_id, Counter())[outcome.status] += 1
    costs = cost_by_model(outcomes)
    return {
        model: {
            "passed": c["passed"],
            "failed": c["failed"],
            "error": c["error"],
            "total": sum(c.values()),
            "cost_usd": round(costs[model][0].cost_usd, 6),
            "judge_cost_usd": round(costs[model][1].cost_usd, 6),
        }
        for model, c in summary.items()
    }


def cost_by_model(outcomes: list[CaseOutcome]) -> dict[str, tuple[TokenUsage, TokenUsage]]:
    """每个模型全部运行（含复核）的 (被测模型用量, 裁判用量)，读不到用量的用例不计入。"""
    totals: dict[str, tuple[TokenUsage, TokenUsage]] = {}
    for outcome in outcomes:
        model_usage, judge_usage = totals.get(outcome.model_id, (TokenUsage(), TokenUsage()))
        totals[outcome.model_id] = (model_usage + (outcome.usage or TokenUsage()), judge_usage + outcome.judge_usage)
    return totals


def finish_suite_run(db: Session, suite_run_id: str, *, status: str, summary: dict) -> None:
    run = db.get(EvalSuiteRun, suite_run_id)
    run.status = status
    run.summary = summary
    run.finished_at = utc_now()
    db.commit()


def previous_results(
    db: Session, *, before: datetime, pairs: set[tuple[str, str]], exclude_suite_run_id: str
) -> dict[tuple[str, str], tuple[str, str]]:
    """每个 (case_id, model_id) 在此前已完成评测里的最近一次 (status, suite_run_id)，同轮取最后一次复核。"""
    if not pairs:
        return {}
    case_ids = {case_id for case_id, _ in pairs}
    model_ids = {model_id for _, model_id in pairs}
    ranked = (
        select(
            EvalCaseResult.case_id,
            EvalCaseResult.model_id,
            EvalCaseResult.status,
            EvalCaseResult.suite_run_id,
            func.row_number()
            .over(
                partition_by=(EvalCaseResult.case_id, EvalCaseResult.model_id),
                order_by=(EvalSuiteRun.started_at.desc(), EvalCaseResult.attempt.desc()),
            )
            .label("rank"),
        )
        .join(EvalSuiteRun, EvalSuiteRun.id == EvalCaseResult.suite_run_id)
        .where(
            EvalSuiteRun.status == "completed",
            EvalSuiteRun.started_at < before,
            EvalSuiteRun.id != exclude_suite_run_id,
            EvalCaseResult.case_id.in_(case_ids),
            EvalCaseResult.model_id.in_(model_ids),
        )
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.case_id, ranked.c.model_id, ranked.c.status, ranked.c.suite_run_id).where(ranked.c.rank == 1)
    ).all()
    return {
        (case_id, model_id): (status, suite_run_id)
        for case_id, model_id, status, suite_run_id in rows
        if (case_id, model_id) in pairs
    }


def compare(
    outcomes: list[CaseOutcome], previous: dict[tuple[str, str], tuple[str, str]]
) -> tuple[list[Change], list[Change]]:
    """返回 (退步, 改善)：之前 passed 现在 failed 为退步，反之为改善；error 不计入两边。"""
    regressions, fixes = [], []
    for outcome in outcomes:
        before = previous.get((outcome.case_id, outcome.model_id))
        if before is None:
            continue
        change = Change(outcome.case_id, outcome.model_id, before[0], outcome.status, before[1])
        if before[0] == "passed" and outcome.status == "failed":
            regressions.append(change)
        elif before[0] == "failed" and outcome.status == "passed":
            fixes.append(change)
    return regressions, fixes


def final_outcomes(outcomes: list[CaseOutcome]) -> list[CaseOutcome]:
    """每个 (用例, 模型) 只保留最后一次运行。"""
    latest: dict[tuple[str, str], CaseOutcome] = {}
    for outcome in outcomes:
        key = (outcome.case_id, outcome.model_id)
        if key not in latest or outcome.attempt >= latest[key].attempt:
            latest[key] = outcome
    return list(latest.values())
