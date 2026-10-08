"""运行时配置 schema 校验。

这里刻意使用轻量规则而不是完整 JSON Schema。运行时配置是主链路依赖，
校验失败时应阻断坏配置生效，而不是阻断聊天服务。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeConfigValidationResult:
    valid: bool
    issues: list[str]


def validate_runtime_config_payload(
    namespace: str,
    key: str,
    payload: Any,
) -> RuntimeConfigValidationResult:
    """校验单个 runtime config payload。

    返回全部可读问题，方便 admin 诊断和自动跳过坏版本。
    """

    issues: list[str] = []
    if not isinstance(payload, dict):
        return RuntimeConfigValidationResult(valid=False, issues=["payload 必须是对象"])

    if namespace == "prompt_template":
        _require_non_empty_string(payload, "template", issues)
    elif namespace == "ui_prompt_catalog" and key == "home":
        _validate_ui_prompt_catalog(payload, issues)
    elif namespace == "agent_strategy" and key == "default":
        _validate_agent_strategy(payload, issues)
    elif namespace == "model_presentation" and key == "default":
        _validate_model_presentation(payload, issues)
    elif namespace == "model_routing" and key == "auto":
        _validate_auto_model_routing(payload, issues)

    return RuntimeConfigValidationResult(valid=not issues, issues=issues)


def _validate_ui_prompt_catalog(payload: dict[str, Any], issues: list[str]) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        issues.append("items 必须是非空数组")
        return

    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{prefix} 必须是对象")
            continue

        for field in ("id", "title", "content", "category", "icon_key", "tone"):
            _require_non_empty_string(item, field, issues, prefix=prefix)

        description = item.get("description")
        if not isinstance(description, str):
            issues.append(f"{prefix}.description 必须是字符串")

        kind = item.get("kind")
        if kind not in {"starter", "template"}:
            issues.append(f"{prefix}.kind 必须是 starter 或 template")

        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen_ids:
                issues.append(f"{prefix}.id 不能重复")
            seen_ids.add(item_id)

        sort_order = item.get("sort_order")
        if not isinstance(sort_order, int) or isinstance(sort_order, bool):
            issues.append(f"{prefix}.sort_order 必须是整数")

        if not isinstance(item.get("enabled"), bool):
            issues.append(f"{prefix}.enabled 必须是布尔值")

        capabilities = item.get("required_capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
            issues.append(f"{prefix}.required_capabilities 必须是字符串数组")


def _validate_agent_strategy(payload: dict[str, Any], issues: list[str]) -> None:
    for field in ("model_runtime", "search", "network", "tool_context"):
        _require_dict(payload, field, issues)

    model_runtime = payload.get("model_runtime")
    if isinstance(model_runtime, dict):
        aliases = model_runtime.get("agent_tools_disabled_aliases")
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            issues.append("model_runtime.agent_tools_disabled_aliases 必须是字符串数组")

    network = payload.get("network")
    if isinstance(network, dict):
        _require_positive_int(network, "max_search_calls", issues, prefix="network")
        _require_positive_int(network, "max_url_read_calls", issues, prefix="network")

    tool_context = payload.get("tool_context")
    if isinstance(tool_context, dict):
        _require_positive_int(tool_context, "max_context_sources", issues, prefix="tool_context")
        _require_positive_int(tool_context, "url_read_max_content_chars", issues, prefix="tool_context")


def _validate_model_presentation(payload: dict[str, Any], issues: list[str]) -> None:
    for field in ("weights", "levels", "copy"):
        _require_dict(payload, field, issues)

    weights = payload.get("weights")
    if isinstance(weights, dict):
        for field in ("base", "network", "vision", "long_context", "deep_thinking"):
            _require_number(weights, field, issues, prefix="weights")

    levels = payload.get("levels")
    if isinstance(levels, dict):
        for field in ("recommended", "capable"):
            _require_number(levels, field, issues, prefix="levels")

    copy_section = payload.get("copy")
    if isinstance(copy_section, dict):
        for field in ("base_reason", "network_tooltip", "no_network_tooltip"):
            _require_non_empty_string(copy_section, field, issues, prefix="copy")


def _validate_auto_model_routing(payload: dict[str, Any], issues: list[str]) -> None:
    # 写入时会与默认值合并，旧的 candidates 等多余字段不拒绝就会被静默忽略
    unknown = sorted(set(payload) - {"providers", "modes"})
    if unknown:
        issues.append(f"不支持的字段：{', '.join(unknown)}（优先级改用 providers 分组）")
    _validate_auto_provider_groups(payload.get("providers"), issues, prefix="providers")
    modes = payload.get("modes")
    if modes is None:
        return
    if not isinstance(modes, dict):
        issues.append("modes 必须是对象")
        return
    for mode, mode_payload in modes.items():
        prefix = f"modes.{mode}"
        if mode not in {"auto", "plan", "deep_research"}:
            issues.append(f"{prefix} 不是支持的执行模式（auto/plan/deep_research）")
            continue
        if not isinstance(mode_payload, dict):
            issues.append(f"{prefix} 必须是对象")
            continue
        mode_unknown = sorted(set(mode_payload) - {"providers"})
        if mode_unknown:
            issues.append(f"{prefix} 不支持的字段：{', '.join(mode_unknown)}")
        _validate_auto_provider_groups(mode_payload.get("providers"), issues, prefix=f"{prefix}.providers")


def _validate_auto_provider_groups(groups: Any, issues: list[str], *, prefix: str) -> None:
    if not isinstance(groups, list) or not groups:
        issues.append(f"{prefix} 必须是非空数组")
        return
    seen_providers: set[str] = set()
    seen_models: set[str] = set()
    for index, group in enumerate(groups):
        group_prefix = f"{prefix}[{index}]"
        if not isinstance(group, dict):
            issues.append(f"{group_prefix} 必须是对象")
            continue
        provider = group.get("provider")
        if not isinstance(provider, str) or not provider:
            issues.append(f"{group_prefix}.provider 必须是非空字符串")
        elif provider in seen_providers:
            issues.append(f"{group_prefix}.provider 重复：{provider}")
        else:
            seen_providers.add(provider)
        models = group.get("models")
        if not isinstance(models, list) or not models or not all(isinstance(item, str) and item for item in models):
            issues.append(f"{group_prefix}.models 必须是非空字符串数组")
            continue
        for model in models:
            if model == "auto":
                issues.append(f"{group_prefix}.models 不能包含 auto")
            elif model in seen_models:
                issues.append(f"{group_prefix}.models 重复：{model}")
            else:
                seen_models.add(model)


def _require_dict(payload: dict[str, Any], field: str, issues: list[str], *, prefix: str = "") -> None:
    value = payload.get(field)
    if not isinstance(value, dict):
        issues.append(f"{_path(prefix, field)} 必须是对象")


def _require_non_empty_string(payload: dict[str, Any], field: str, issues: list[str], *, prefix: str = "") -> None:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{_path(prefix, field)} 必须是非空字符串")


def _require_number(payload: dict[str, Any], field: str, issues: list[str], *, prefix: str = "") -> None:
    value = payload.get(field)
    if not isinstance(value, int | float):
        issues.append(f"{_path(prefix, field)} 必须是数字")


def _require_positive_int(payload: dict[str, Any], field: str, issues: list[str], *, prefix: str = "") -> None:
    value = payload.get(field)
    if not isinstance(value, int) or value <= 0:
        issues.append(f"{_path(prefix, field)} 必须是正整数")


def _path(prefix: str, field: str) -> str:
    return f"{prefix}.{field}" if prefix else field
