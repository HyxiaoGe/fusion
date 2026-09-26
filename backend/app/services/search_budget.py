"""联网搜索预算策略。

模型决定结果数量，服务端只应用请求和上下文的硬上限。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchBudget:
    name: str
    requested_count: int
    context_source_limit: int


@dataclass(frozen=True)
class SearchBudgetDecision:
    query: str
    intent: str | None
    action: str
    budget_name: str
    requested_count: int
    context_source_limit: int
    reason_code: str
    previous_query_count: int
    planned_search_limit: int


DEFAULT_SEARCH_COUNT = 10
MAX_SEARCH_COUNT = 20
MAX_CONTEXT_SOURCES = 10
SUPPORTED_SEARCH_INTENTS = {"quick_fact", "freshness", "comparison", "deep_research", "official_source"}


def normalize_search_intent(value, *, strategy_config: dict | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    intent = value.strip().lower()
    if intent in SUPPORTED_SEARCH_INTENTS:
        return intent
    return None


def resolve_search_intent(value, query: str | None = None, *, strategy_config: dict | None = None) -> str | None:
    """只接收模型显式 intent；缺失时由预算层使用 standard。"""

    return normalize_search_intent(value, strategy_config=strategy_config)


def derive_search_budget(
    intent: str | None,
    *,
    requested_count: object = None,
) -> SearchBudget:
    """旧配置的意图/追问小预算不再覆写模型数量，避免升级后仍被 DB 旧值压低。"""

    try:
        count = int(requested_count) if not isinstance(requested_count, bool) else DEFAULT_SEARCH_COUNT
    except (TypeError, ValueError, OverflowError):
        count = DEFAULT_SEARCH_COUNT
    count = max(1, min(MAX_SEARCH_COUNT, count))
    return SearchBudget(
        name=intent if intent in SUPPORTED_SEARCH_INTENTS else "standard",
        requested_count=count,
        context_source_limit=min(count, MAX_CONTEXT_SOURCES),
    )
