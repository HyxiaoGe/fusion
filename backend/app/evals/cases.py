"""回放评测用例：YAML 定义、严格校验与加载。

每个 YAML 文件是一个类别，形如：

    category: escalation
    cases:
      - id: weekend-shanghai-kids
        source: escalation-probe 2026-10-04（#259）
        message: 周末带孩子在上海玩一天，怎么安排比较好
        checks:
          - type: escalation
            expect: either
            targets: [mixed_itinerary, place_discovery, weather]

检查项只描述"该做到什么、不能出什么错"，不写标准答案；能用轨迹判的不交给裁判模型。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CASES_DIR = Path(__file__).resolve().parents[2] / "evals" / "cases"

_CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,80}$"


class _Check(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirstPackageCheck(_Check):
    """首轮能力包（分类器的首判）落在给定集合内。"""

    type: Literal["first_package"]
    any_of: list[str] = Field(min_length=1)


class EscalationCheck(_Check):
    """Run 内能力升级：escalate 必须升级，none 不得申请也不得升级，either 都可以；
    targets 非空时，发生的第一次升级必须落在其中。"""

    type: Literal["escalation"]
    expect: Literal["escalate", "none", "either"]
    targets: list[str] = Field(default_factory=list)


class ToolCalledCheck(_Check):
    type: Literal["tool_called"]
    tool: str
    min_count: int = Field(default=1, ge=1)
    max_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _bounds(self) -> ToolCalledCheck:
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("max_count 不能小于 min_count")
        return self


class ToolNotCalledCheck(_Check):
    type: Literal["tool_not_called"]
    tool: str


class ToolArgCheck(_Check):
    """该工具至少有一次调用的参数满足条件；field 支持点号路径。"""

    type: Literal["tool_arg"]
    tool: str
    field: str
    equals: Any = None
    any_of: list[Any] | None = None
    contains: str | None = None

    @model_validator(mode="after")
    def _one_condition(self) -> ToolArgCheck:
        given = [self.equals is not None, self.any_of is not None, self.contains is not None]
        if sum(given) != 1:
            raise ValueError("equals / any_of / contains 必须且只能给一个")
        return self


class SkillsCheck(_Check):
    """none：本轮不得挂载任何 Skill；loaded：至少挂载一个，names 非空时须全部挂载。"""

    type: Literal["skills"]
    expect: Literal["none", "loaded"]
    names: list[str] = Field(default_factory=list)


class AnswerBlockCheck(_Check):
    """最终回答里是否出现某类内容块，如 document、weather_results。"""

    type: Literal["answer_block"]
    block_type: str
    present: bool = True


class RunStatusCheck(_Check):
    """覆盖默认的"run 必须 completed"要求。"""

    type: Literal["run_status"]
    any_of: list[str] = Field(min_length=1)


class JudgeCheck(_Check):
    """程序判不了的语义要求，交给裁判模型按评分标准判定。"""

    type: Literal["judge"]
    rubric: str = Field(min_length=10)


Check = Annotated[
    Union[
        FirstPackageCheck,
        EscalationCheck,
        ToolCalledCheck,
        ToolNotCalledCheck,
        ToolArgCheck,
        SkillsCheck,
        AnswerBlockCheck,
        RunStatusCheck,
        JudgeCheck,
    ],
    Field(discriminator="type"),
]


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_CASE_ID_PATTERN)
    category: str
    source: str = Field(min_length=3)
    message: str = Field(min_length=1)
    setup_turns: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    models: list[str] | None = None
    checks: list[Check] = Field(min_length=1)

    def applies_to(self, model_id: str) -> bool:
        return self.models is None or model_id in self.models


class _CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    cases: list[dict[str, Any]] = Field(min_length=1)


class EvalSuite(BaseModel):
    cases: list[EvalCase]
    sha256: str

    def select(self, *, case_ids: set[str] | None = None, categories: set[str] | None = None) -> list[EvalCase]:
        chosen = [
            case
            for case in self.cases
            if (case_ids is None or case.id in case_ids) and (categories is None or case.category in categories)
        ]
        if case_ids:
            missing = case_ids - {case.id for case in chosen}
            if missing:
                raise ValueError(f"未知用例: {', '.join(sorted(missing))}")
        return chosen


def load_suite(cases_dir: Path = CASES_DIR) -> EvalSuite:
    files = sorted(cases_dir.glob("*.yaml"))
    if not files:
        raise ValueError(f"{cases_dir} 下没有用例文件")
    digest = hashlib.sha256()
    cases: list[EvalCase] = []
    seen: dict[str, str] = {}
    for path in files:
        raw = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + raw + b"\0")
        parsed = _CaseFile.model_validate(yaml.safe_load(raw))
        for item in parsed.cases:
            case = EvalCase.model_validate({**item, "category": parsed.category})
            if case.id in seen:
                raise ValueError(f"用例 id 重复: {case.id}（{seen[case.id]} 与 {path.name}）")
            seen[case.id] = path.name
            cases.append(case)
    return EvalSuite(cases=cases, sha256=digest.hexdigest())
