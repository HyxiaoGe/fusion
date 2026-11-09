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
STANDARD_SEARCH_BUDGET = "standard"


def derive_search_budget(*, requested_count: object = None) -> SearchBudget:
    """模型定条数；这里只算进上下文的条数（最多 10 条），条数是否合法由搜索服务检查。"""

    try:
        count = int(requested_count) if not isinstance(requested_count, bool) else DEFAULT_SEARCH_COUNT
    except (TypeError, ValueError, OverflowError):
        count = DEFAULT_SEARCH_COUNT
    count = max(1, min(MAX_SEARCH_COUNT, count))
    return SearchBudget(
        name=STANDARD_SEARCH_BUDGET,
        requested_count=count,
        context_source_limit=min(count, MAX_CONTEXT_SOURCES),
    )
