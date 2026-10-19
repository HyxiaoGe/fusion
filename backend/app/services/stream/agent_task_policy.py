"""Agent 任务模式的唯一受控策略解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.agent.plan_coordinator import PlanMode, normalize_plan_mode


@dataclass(frozen=True)
class AgentTaskPolicy:
    plan_mode: PlanMode

    def apply_to_options(self, options: dict | None = None) -> dict:
        return {**(options or {}), "plan_mode": self.plan_mode}


def resolve_agent_task_policy(*, options: dict | None) -> AgentTaskPolicy:
    return AgentTaskPolicy(plan_mode=normalize_plan_mode((options or {}).get("plan_mode")))


def restore_agent_task_policy(run_config: dict | None) -> AgentTaskPolicy:
    """从可信 run config 恢复策略；旧 run 缺字段时保持原 plan_mode 语义。"""

    config: dict[str, Any] = run_config if isinstance(run_config, dict) else {}
    return resolve_agent_task_policy(options={"plan_mode": config.get("plan_mode")})
