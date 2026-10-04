"""按用例检查项对 run 快照判分。

除 judge 外都只读快照里的结构化字段（能力包、工具名、工具参数、内容块类型），
不对回答文本做关键词或正则匹配；回答是否满足语义要求只交给裁判模型。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.evals.cases import (
    AnswerBlockCheck,
    Check,
    EscalationCheck,
    EvalCase,
    FirstPackageCheck,
    JudgeCheck,
    RunStatusCheck,
    SkillsCheck,
    ToolArgCheck,
    ToolCalledCheck,
    ToolNotCalledCheck,
)

CheckStatus = Literal["passed", "failed", "error"]


@dataclass(frozen=True)
class CheckOutcome:
    type: str
    status: CheckStatus
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class JudgeVerdict:
    passed: bool
    reason: str


Judge = Callable[[JudgeCheck, EvalCase, Mapping[str, Any]], Awaitable[JudgeVerdict]]


def _outcome(check_type: str, passed: bool, detail: str) -> CheckOutcome:
    return CheckOutcome(check_type, "passed" if passed else "failed", detail)


def _calls(snapshot: Mapping[str, Any], tool: str) -> list[Mapping[str, Any]]:
    return [call for call in snapshot.get("tool_calls") or [] if call.get("tool") == tool]


def _field(arguments: Any, path: str) -> tuple[bool, Any]:
    current = arguments
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _check_run_status(check: RunStatusCheck | None, snapshot: Mapping[str, Any]) -> CheckOutcome:
    allowed = check.any_of if check is not None else ["completed"]
    status = snapshot.get("status")
    return _outcome("run_status", status in allowed, f"status={status}, 期望 {allowed}")


def _check_first_package(check: FirstPackageCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    package = snapshot.get("first_package")
    return _outcome(check.type, package in check.any_of, f"首判={package}, 期望 {check.any_of}")


def _check_escalation(check: EscalationCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    targets = [item.get("to") for item in snapshot.get("escalations") or []]
    requested = bool(_calls(snapshot, "request_capability"))
    if check.expect == "none":
        return _outcome(
            check.type,
            not targets and not requested,
            f"升级={targets or '无'}, 申请能力={'是' if requested else '否'}",
        )
    if not targets:
        return _outcome(check.type, check.expect == "either", "未升级")
    if check.targets and targets[0] not in check.targets:
        return _outcome(check.type, False, f"升级到 {targets[0]}, 期望 {check.targets}")
    return _outcome(check.type, True, f"升级到 {targets}")


def _check_tool_called(check: ToolCalledCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    count = len(_calls(snapshot, check.tool))
    upper = check.max_count if check.max_count is not None else count
    bound = f"[{check.min_count}, {check.max_count if check.max_count is not None else '∞'}]"
    return _outcome(check.type, check.min_count <= count <= upper, f"{check.tool} 调用 {count} 次, 期望 {bound}")


def _check_tool_not_called(check: ToolNotCalledCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    count = len(_calls(snapshot, check.tool))
    return _outcome(check.type, count == 0, f"{check.tool} 调用 {count} 次, 期望 0")


def _arg_matches(check: ToolArgCheck, value: Any) -> bool:
    if check.any_of is not None:
        return value in check.any_of
    if check.contains is not None:
        return isinstance(value, str) and check.contains in value
    return value == check.equals


def _check_tool_arg(check: ToolArgCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    observed = []
    for call in _calls(snapshot, check.tool):
        found, value = _field(call.get("arguments"), check.field)
        if found and _arg_matches(check, value):
            return _outcome(check.type, True, f"{check.tool}.{check.field}={value!r}")
        observed.append(value if found else "<缺失>")
    if not observed:
        return _outcome(check.type, False, f"{check.tool} 未被调用")
    return _outcome(check.type, False, f"{check.tool}.{check.field} 实际为 {observed}")


def _check_skills(check: SkillsCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    loaded = list(snapshot.get("skills") or [])
    if check.expect == "none":
        return _outcome(check.type, not loaded, f"已挂载 {loaded or '无'}")
    missing = [name for name in check.names if name not in loaded]
    return _outcome(check.type, bool(loaded) and not missing, f"已挂载 {loaded or '无'}, 缺少 {missing or '无'}")


def _check_answer_block(check: AnswerBlockCheck, snapshot: Mapping[str, Any]) -> CheckOutcome:
    blocks = list(snapshot.get("answer_blocks") or [])
    present = check.block_type in blocks
    return _outcome(
        check.type,
        present == check.present,
        f"回答块 {blocks}, 期望{'包含' if check.present else '不含'} {check.block_type}",
    )


_DETERMINISTIC = {
    FirstPackageCheck: _check_first_package,
    EscalationCheck: _check_escalation,
    ToolCalledCheck: _check_tool_called,
    ToolNotCalledCheck: _check_tool_not_called,
    ToolArgCheck: _check_tool_arg,
    SkillsCheck: _check_skills,
    AnswerBlockCheck: _check_answer_block,
}


async def evaluate(case: EvalCase, snapshot: Mapping[str, Any], judge: Judge | None) -> list[CheckOutcome]:
    """默认要求 run completed；用例给了 run_status 就以它为准。"""
    status_check = next((check for check in case.checks if isinstance(check, RunStatusCheck)), None)
    outcomes = [_check_run_status(status_check, snapshot)]
    for check in case.checks:
        if isinstance(check, RunStatusCheck):
            continue
        if isinstance(check, JudgeCheck):
            outcomes.append(await _run_judge(check, case, snapshot, judge))
            continue
        outcomes.append(_DETERMINISTIC[type(check)](check, snapshot))
    return outcomes


async def _run_judge(
    check: JudgeCheck, case: EvalCase, snapshot: Mapping[str, Any], judge: Judge | None
) -> CheckOutcome:
    if judge is None:
        return CheckOutcome(check.type, "error", "未启用裁判模型")
    if not (snapshot.get("answer_text") or "").strip():
        return _outcome(check.type, False, "没有文字回答可供评审")
    try:
        verdict = await judge(check, case, snapshot)
    except Exception as exc:  # 裁判自身故障记为 error，不算被测模型失败
        return CheckOutcome(check.type, "error", f"裁判调用失败: {type(exc).__name__}: {exc}"[:500])
    return _outcome(check.type, verdict.passed, verdict.reason[:500])


def overall_status(outcomes: list[CheckOutcome]) -> CheckStatus:
    if any(item.status == "failed" for item in outcomes):
        return "failed"
    if any(item.status == "error" for item in outcomes):
        return "error"
    return "passed"


__all__ = ["Check", "CheckOutcome", "Judge", "JudgeVerdict", "evaluate", "overall_status"]
