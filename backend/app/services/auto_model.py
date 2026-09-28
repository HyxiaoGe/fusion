"""自动选择模型。

会话绑定 `auto`，或绑定的模型已从目录下线/被禁止调用时，每轮在服务端按管理员配置的
优先级挑一个当前可调用的具体模型。只看目录、调度开关、健康状态和本轮的硬性需求（带图
就要读图），不根据问题内容猜模型。候选全部不可用时退到目录里其余可用模型，保证只要
目录里还有模型，对话就不会因为模型下线而卡死。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.ai import litellm_catalog, litellm_health
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.services.agent_strategy_config import get_agent_tools_disabled_aliases
from app.services.runtime_config_defaults import DEFAULT_AUTO_MODEL_CONFIG
from app.services.runtime_config_service import get_runtime_config_payload

AUTO_MODEL_ID = "auto"

# 与 /api/models 的排序一致：候选之外的兜底按 cost_tier 从低到高挑
_COST_TIER_ORDER = {"low": 0, "mid": 1, "high": 2}


@dataclass(frozen=True)
class ModelResolution:
    """本轮实际调用的模型；fallback_from 记录因下线被替换掉的原绑定模型。"""

    model_id: str
    fallback_from: str | None = None


def get_auto_model_candidates() -> list[str]:
    payload, _ = get_runtime_config_payload("model_routing", "auto", DEFAULT_AUTO_MODEL_CONFIG)
    candidates = payload.get("candidates") if isinstance(payload, Mapping) else None
    if not isinstance(candidates, list):
        candidates = DEFAULT_AUTO_MODEL_CONFIG["candidates"]
    return [str(item) for item in candidates if isinstance(item, str) and item]


def is_model_registered(model_id: str) -> bool:
    """模型在 LiteLLM 目录里注册为真实模型；目录拉不到时无法判断，按已注册处理。"""

    entry = litellm_catalog.get_model_entry(model_id)
    if isinstance(entry, Mapping) and entry.get("db_model"):
        return True
    status = litellm_catalog.get_cache_status()
    return not (status.get("availability") == "available" or status.get("has_cache"))


def pick_auto_model(
    controls: ModelCatalogControlRepository,
    *,
    require_vision: bool = False,
) -> str | None:
    """按优先级挑一个可调用模型；没有可读图的模型时放宽读图要求，由无视觉边界兜底。"""

    catalog = litellm_catalog.list_aliases()
    candidates = get_auto_model_candidates()
    if not catalog:
        # 目录暂时拉不到：无法校验可用性，按配置顺序直接交给 LiteLLM
        return candidates[0] if candidates else None

    db_aliases = [alias for alias, entry in catalog.items() if isinstance(entry, Mapping) and entry.get("db_model")]
    rest = sorted(
        (alias for alias in db_aliases if alias not in candidates),
        key=lambda alias: (_COST_TIER_ORDER.get(_cost_tier(catalog[alias]), 5), alias),
    )
    ordered = [alias for alias in candidates if alias in db_aliases] + rest
    controls_by_model = controls.get_by_model_ids(ordered)
    usable = [alias for alias in ordered if _is_usable(alias, controls_by_model.get(alias))]
    if require_vision:
        disabled_aliases = get_agent_tools_disabled_aliases()
        with_vision = [
            alias
            for alias in usable
            if litellm_catalog.get_capabilities(alias, agent_tools_disabled_aliases=disabled_aliases).get("vision")
        ]
        if with_vision:
            return with_vision[0]
    return usable[0] if usable else None


def _is_usable(alias: str, control: Any) -> bool:
    if control is not None and (
        getattr(control, "routable", True) is False or getattr(control, "selectable", True) is False
    ):
        return False
    return litellm_health.get_health(alias).get("status") != "unhealthy"


def _cost_tier(entry: Mapping[str, Any]) -> str:
    metadata = entry.get("metadata") or {}
    return str(metadata.get("cost_tier") or "mid")
