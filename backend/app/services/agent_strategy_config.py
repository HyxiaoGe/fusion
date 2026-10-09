"""Agent 联网策略配置访问。"""

from __future__ import annotations

import copy
from typing import Any

from app.services.config_defaults import DEFAULT_AGENT_STRATEGY_CONFIG


def get_agent_strategy_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_AGENT_STRATEGY_CONFIG)


def get_agent_tools_disabled_aliases() -> set[str]:
    aliases = get_agent_strategy_config().get("model_runtime", {}).get("agent_tools_disabled_aliases") or []
    return {str(alias) for alias in aliases}
