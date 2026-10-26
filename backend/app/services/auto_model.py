"""自动选择模型。

会话绑定 `auto`，或绑定的模型已从目录下线/被禁止调用时，每轮在服务端按管理员配置的
优先级挑一个当前可调用的具体模型。优先级按「提供商 → 模型 ID」分组配置，目前组与组内
都按顺序取第一个可用模型。只看目录、调度开关、健康状态、模型熔断和本轮的硬性需求
（带图就要读图），不根据问题内容猜模型。
候选全部不可用时退到目录里其余可用模型，保证只要目录里还有模型，对话就不会因为模型
下线而卡死。原模型在回答中途不可用时，也从这里挑能力满足、换一家提供商的备用模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.ai import litellm_catalog, litellm_health
from app.ai.model_circuit_breaker import model_circuit_breaker
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.services.agent_strategy_config import get_agent_tools_disabled_aliases
from app.services.config_defaults import DEFAULT_AUTO_MODEL_CONFIG
from app.services.credit_pricing import cost_tier

AUTO_MODEL_ID = "auto"

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


def get_auto_provider_groups() -> list[AutoProviderGroup]:
    return _parse_provider_groups(DEFAULT_AUTO_MODEL_CONFIG)


def get_auto_model_candidates() -> list[str]:
    """把提供商分组按顺序展开成候选列表；组内目前同样按顺序取第一个可用模型。"""

    return [model for group in get_auto_provider_groups() for model in group.models]


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
    allowed_model_ids: tuple[str, ...] | None = None,
) -> str | None:
    """按自动选择的优先级挑一个可调用模型。

    带图时优先挑能读图的；没有任何候选能读图时仍返回第一个可用模型，由无视觉边界兜底。
    allowed_model_ids：只在这些模型里挑（游客），排在自动选择候选之后按给定顺序补齐，
    不再退到目录里的其他模型。
    """

    catalog = litellm_catalog.list_aliases()
    candidates = _restrict_to_allowed(get_auto_model_candidates(), allowed_model_ids)
    if not catalog:
        # 目录暂时拉不到：无法校验可用性，按配置顺序直接交给 LiteLLM
        return candidates[0] if candidates else None

    ordered = _registered_in_priority(catalog, candidates, include_rest=allowed_model_ids is None)
    # 熔断中的模型排到最后：仍是兜底候选，只是不再优先挑一个正在连续失败的模型。
    ordered.sort(key=model_circuit_breaker.is_open)
    controls_by_model = controls.get_by_model_ids(ordered)
    usable = [alias for alias in ordered if _is_usable(alias, controls_by_model.get(alias))]
    if require_vision:
        disabled_aliases = get_agent_tools_disabled_aliases()
        for alias in usable:
            capabilities = litellm_catalog.get_capabilities(alias, agent_tools_disabled_aliases=disabled_aliases)
            if _has_capabilities(capabilities, ("vision",)):
                return alias
    return usable[0] if usable else None


def pick_fallback_model(
    controls: ModelCatalogControlRepository | None,
    *,
    failed_model_id: str,
    preferred_model_id: str,
    required_capabilities: tuple[str, ...],
    allowed_model_ids: tuple[str, ...] | None = None,
) -> str | None:
    """原模型本轮不可用时挑一个备用模型，挑不到返回 None。

    与自动选择不同，能力要求不放宽：换过去的模型读不了图、调不了工具，换了也是失败。
    跳过与原模型同一服务平台的模型（平台整体故障时上面的模型多半一起不可用）和熔断中的模型。
    优先 preferred_model_id，其次按自动选择的候选顺序，再按成本从低到高；游客只在
    allowed_model_ids 里挑。目录拉不到时无法核对能力与提供商，不换。
    """

    catalog = litellm_catalog.list_aliases()
    if not catalog:
        return None
    candidates = _restrict_to_allowed([preferred_model_id, *get_auto_model_candidates()], allowed_model_ids)
    ordered = _registered_in_priority(catalog, candidates, include_rest=allowed_model_ids is None)
    failed_provider = _serving_provider(catalog.get(failed_model_id))
    controls_by_model = controls.get_by_model_ids(ordered) if controls is not None else {}
    disabled_aliases = get_agent_tools_disabled_aliases()
    for alias in ordered:
        if alias == failed_model_id or model_circuit_breaker.is_open(alias):
            continue
        if failed_provider and _serving_provider(catalog[alias]) == failed_provider:
            continue
        if not _is_usable(alias, controls_by_model.get(alias)):
            continue
        capabilities = litellm_catalog.get_capabilities(alias, agent_tools_disabled_aliases=disabled_aliases)
        if _has_capabilities(capabilities, required_capabilities):
            return alias
    return None


def _restrict_to_allowed(candidates: list[str], allowed_model_ids: tuple[str, ...] | None) -> list[str]:
    if allowed_model_ids is None:
        return list(dict.fromkeys(candidates))
    return list(dict.fromkeys([*(m for m in candidates if m in allowed_model_ids), *allowed_model_ids]))


def _registered_in_priority(
    catalog: Mapping[str, Any],
    candidates: list[str],
    *,
    include_rest: bool,
) -> list[str]:
    """候选里已注册的按给定顺序在前；include_rest 时目录里其余模型按成本从低到高补在后面。"""

    db_aliases = [alias for alias, entry in catalog.items() if isinstance(entry, Mapping) and entry.get("db_model")]
    rest = (
        sorted(
            (alias for alias in db_aliases if alias not in candidates),
            key=lambda alias: (_COST_TIER_ORDER.get(_cost_tier(catalog[alias]), 5), alias),
        )
        if include_rest
        else []
    )
    return [alias for alias in candidates if alias in db_aliases] + rest


def _serving_provider(entry: Any) -> str:
    """提供服务的平台。很多模型走 OpenAI 兼容接口，底层前缀都是 openai 分不出平台，
    所以优先用目录元数据的 provider_key（同一平台上的不同厂商模型归为一家）。"""

    if not isinstance(entry, Mapping):
        return ""
    metadata = entry.get("metadata") or {}
    provider_key = str(metadata.get("provider_key") or "").strip().lower()
    if provider_key:
        return provider_key
    underlying = str(entry.get("underlying") or "")
    return underlying.split("/", 1)[0].lower() if "/" in underlying else ""


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
    return cost_tier(entry.get("token_costs") or {}) or str(metadata.get("cost_tier") or "mid")
