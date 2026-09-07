"""联网搜索预算策略。

模型决定结果数量，服务端只应用请求和上下文的硬上限。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.services.agent_strategy_config import get_agent_strategy_config


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

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]+")
_CURRENT_YEAR_RE = re.compile(r"(?<!\d)20\d{2}(?!\d)")

_COMPARISON_KEYWORDS = (
    "权威媒体",
    "媒体",
    "报道",
    "对照",
    "对比",
    "比较",
    "compare",
    "comparison",
    "versus",
    "media",
    "reuters",
    "bloomberg",
    "techcrunch",
    "axios",
    "bbc",
    "nytimes",
    "new york times",
    "wall street journal",
    "wsj",
    "the verge",
)
_OFFICIAL_SOURCE_KEYWORDS = (
    "官方",
    "官网",
    "公告",
    "发布",
    "official",
    "announcement",
    "announces",
    "announced",
    "press release",
    "release notes",
    "official blog",
    "openai.com",
)
_DEEP_RESEARCH_KEYWORDS = (
    "深入",
    "调研",
    "研究",
    "论文",
    "白皮书",
    "技术报告",
    "technical report",
    "system card",
    "research",
    "paper",
    "whitepaper",
)
_FRESHNESS_KEYWORDS = (
    "最新",
    "今天",
    "今日",
    "目前",
    "实时",
    "current",
    "latest",
    "today",
    "recent",
    "new",
)
_QUICK_FACT_KEYWORDS = (
    "是谁",
    "是什么",
    "多少",
    "价格",
    "上市日期",
    "who is",
    "what is",
    "when did",
    "how much",
)


def normalize_search_intent(value, *, strategy_config: dict | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    intent = value.strip().lower()
    if intent in _supported_search_intents(strategy_config):
        return intent
    return None


def resolve_search_intent(value, query: str | None = None, *, strategy_config: dict | None = None) -> str | None:
    """优先使用模型显式 intent，否则从 query 里做保守推断。"""

    explicit_intent = normalize_search_intent(value, strategy_config=strategy_config)
    if explicit_intent:
        return explicit_intent
    return infer_search_intent(query or "", strategy_config=strategy_config)


def infer_search_intent(query: str, *, strategy_config: dict | None = None) -> str | None:
    normalized = _normalize_query_text(query)
    if not normalized:
        return None

    keywords = _search_config(strategy_config).get("intent_keywords", {})
    comparison_keywords = tuple(keywords.get("comparison") or _COMPARISON_KEYWORDS)
    official_source_keywords = tuple(keywords.get("official_source") or _OFFICIAL_SOURCE_KEYWORDS)
    deep_research_keywords = tuple(keywords.get("deep_research") or _DEEP_RESEARCH_KEYWORDS)
    freshness_keywords = tuple(keywords.get("freshness") or _FRESHNESS_KEYWORDS)
    quick_fact_keywords = tuple(keywords.get("quick_fact") or _QUICK_FACT_KEYWORDS)

    if _contains_any(normalized, comparison_keywords):
        return "comparison"
    if _contains_any(normalized, official_source_keywords):
        return "official_source"
    if _contains_any(normalized, deep_research_keywords):
        return "deep_research"
    if _contains_any(normalized, freshness_keywords) or _CURRENT_YEAR_RE.search(normalized):
        return "freshness"
    if _contains_any(normalized, quick_fact_keywords) and len(_query_tokens(normalized)) <= 8:
        return "quick_fact"
    return None


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


def _query_tokens(query: str) -> set[str]:
    normalized = _normalize_query_text(query)
    tokens = set(_LATIN_TOKEN_RE.findall(normalized))
    for sequence in _CJK_SEQUENCE_RE.findall(normalized):
        if len(sequence) == 1:
            tokens.add(sequence)
            continue
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def _search_config(strategy_config: dict | None = None) -> dict:
    if strategy_config is None:
        strategy_config, _meta = get_agent_strategy_config()
    return strategy_config.get("search") or {}


def _supported_search_intents(strategy_config: dict | None = None) -> set[str]:
    search_config = _search_config(strategy_config)
    configured = search_config.get("budgets_by_intent")
    if isinstance(configured, dict) and configured:
        return set(configured)
    return SUPPORTED_SEARCH_INTENTS


def _normalize_query_text(query: str) -> str:
    return str(query or "").strip().lower()


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword in text for keyword in keywords)
