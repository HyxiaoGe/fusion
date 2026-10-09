"""自动选择模型。

会话绑定 `auto`，或绑定的模型已从目录下线/被禁止调用时，每轮在服务端按管理员配置的
优先级挑一个当前可调用的具体模型。优先级按「提供商 → 模型 ID」分组配置，目前组与组内
都按顺序取第一个可用模型；用户本轮选的执行模式（自动/规划执行/深度研究）可以各配一套
优先级，没配的模式沿用默认分组。只看目录、调度开关、健康状态、执行模式和本轮的硬性
需求（带图就要读图，规划执行要工具调用，深度研究还要联网工具），不根据问题内容猜模型。
候选全部不可用时退到目录里其余可用模型，保证只要目录里还有模型，对话就不会因为模型
下线而卡死。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from app.ai import litellm_catalog, litellm_health
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.services.agent_strategy_config import get_agent_tools_disabled_aliases
from app.services.config_defaults import DEFAULT_AUTO_MODEL_CONFIG

AUTO_MODEL_ID = "auto"

# 与前端执行模式一一对应：auto=自动，plan=规划执行，deep_research=深度研究
AutoModelMode = Literal["auto", "plan", "deep_research"]
AUTO_MODEL_MODES: tuple[AutoModelMode, ...] = get_args(AutoModelMode)

# 执行模式对模型的硬性能力要求；不满足的候选本轮跳过
_MODE_REQUIRED_CAPABILITIES: dict[AutoModelMode, tuple[str, ...]] = {
    "auto": (),
    "plan": ("functionCalling",),
    "deep_research": ("functionCalling", "searchCapable"),
}

# 与 /api/models 的排序一致：候选之外的兜底按 cost_tier 从低到高挑
_COST_TIER_ORDER = {"low": 0, "mid": 1, "high": 2}


@dataclass(frozen=True)
class ModelResolution:
    """本轮实际调用的模型；fallback_from 记录因下线被替换掉的原绑定模型。"""

    model_id: str
    fallback_from: str | None = None


@dataclass(frozen=True)
class AutoProviderGroup:
    """一个提供商下参与自动选择的模型，按优先级排列。"""

    provider: str
    models: tuple[str, ...]


def get_auto_provider_groups(mode: AutoModelMode = "auto") -> list[AutoProviderGroup]:
    return _mode_provider_groups(DEFAULT_AUTO_MODEL_CONFIG, mode)


def get_auto_model_candidates(mode: AutoModelMode = "auto") -> list[str]:
    """把提供商分组按顺序展开成候选列表；组内目前同样按顺序取第一个可用模型。"""

    return [model for group in get_auto_provider_groups(mode) for model in group.models]


def get_all_auto_model_candidates() -> list[str]:
    """所有执行模式的候选并集（保持首次出现的顺序），供自动选择卡片汇总能力。"""

    return list(dict.fromkeys(model for mode in AUTO_MODEL_MODES for model in get_auto_model_candidates(mode)))


def _mode_provider_groups(payload: Any, mode: AutoModelMode) -> list[AutoProviderGroup]:
    """取执行模式自己的分组；该模式没配或配置无效时沿用默认 providers。"""

    modes = payload.get("modes") if isinstance(payload, Mapping) else None
    mode_payload = modes.get(mode) if isinstance(modes, Mapping) else None
    return _parse_provider_groups(mode_payload) or _parse_provider_groups(payload)


def _parse_provider_groups(payload: Any) -> list[AutoProviderGroup]:
    raw_groups = payload.get("providers") if isinstance(payload, Mapping) else None
    if not isinstance(raw_groups, list):
        return []
    groups: list[AutoProviderGroup] = []
    for raw in raw_groups:
        if not isinstance(raw, Mapping):
            continue
        provider = raw.get("provider")
        models = raw.get("models")
        if not isinstance(provider, str) or not provider or not isinstance(models, list):
            continue
        model_ids = tuple(str(item) for item in models if isinstance(item, str) and item)
        if model_ids:
            groups.append(AutoProviderGroup(provider=provider, models=model_ids))
    return groups


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
    mode: AutoModelMode = "auto",
) -> str | None:
    """按执行模式的优先级挑一个可调用模型。

    执行模式要求的能力是硬性的：没有任何候选满足时仍返回第一个可用模型，交给任务策略
    按原有规则报错或降级，不在这里掩盖。读图同理放宽，由无视觉边界兜底。
    """

    catalog = litellm_catalog.list_aliases()
    candidates = get_auto_model_candidates(mode)
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
    mode_required = _MODE_REQUIRED_CAPABILITIES[mode]
    if mode_required or require_vision:
        disabled_aliases = get_agent_tools_disabled_aliases()
        capabilities = {
            alias: litellm_catalog.get_capabilities(alias, agent_tools_disabled_aliases=disabled_aliases)
            for alias in usable
        }
        # 先找同时满足执行模式与读图的；读图可以放宽，执行模式要求的能力仍优先满足
        attempts = ((*mode_required, "vision"), mode_required) if require_vision else (mode_required,)
        for required in attempts:
            capable = [alias for alias in usable if _has_capabilities(capabilities[alias], required)]
            if capable:
                return capable[0]
    return usable[0] if usable else None


def _has_capabilities(capabilities: Mapping[str, Any], required: tuple[str, ...]) -> bool:
    return all(capabilities.get(key) is True for key in required)


def _is_usable(alias: str, control: Any) -> bool:
    if control is not None and (
        getattr(control, "routable", True) is False or getattr(control, "selectable", True) is False
    ):
        return False
    return litellm_health.get_health(alias).get("status") != "unhealthy"


def _cost_tier(entry: Mapping[str, Any]) -> str:
    metadata = entry.get("metadata") or {}
    return str(metadata.get("cost_tier") or "mid")
