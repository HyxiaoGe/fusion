"""单轮联网工具预算。

web_search 的参数（条数、时效、限定网站）由搜索服务检查，出错时服务告诉模型哪里不对；
这里不改写模型的参数，只管本轮调用次数和进上下文的条数。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.services.agent_strategy_config import get_agent_strategy_config
from app.services.search_budget import SearchBudgetDecision, derive_search_budget
from app.services.tool_handlers.base import ToolResult

MAX_SEARCH_CALLS = 40
MAX_URL_READ_CALLS = 100


@dataclass
class NetworkToolBudget:
    """限制一次 assistant run 内的联网工具调用次数。"""

    web_search_calls: int = 0
    url_read_calls: int = 0
    web_search_queries: list[str] = field(default_factory=list)

    def prepare_web_search_args(self, args: dict) -> tuple[dict, ToolResult | None]:
        network_config = _network_config(get_agent_strategy_config())
        normalized = dict(args or {})
        query = str(normalized.get("query") or "")
        max_search_calls = _network_int(network_config, "max_search_calls", MAX_SEARCH_CALLS)
        previous_query_count = len(self.web_search_queries)

        search_budget = derive_search_budget(requested_count=normalized.get("count"))
        normalized["context_source_limit"] = search_budget.context_source_limit
        normalized["search_budget"] = search_budget.name
        limit_reached = self.web_search_calls >= max_search_calls
        decision = asdict(
            SearchBudgetDecision(
                query=query,
                action="limit_budget" if limit_reached else "execute",
                budget_name=search_budget.name,
                requested_count=search_budget.requested_count,
                context_source_limit=search_budget.context_source_limit,
                reason_code=(
                    "hard_search_limit_reached"
                    if limit_reached
                    else ("complementary_search" if previous_query_count > 0 else "initial_search")
                ),
                previous_query_count=previous_query_count,
                planned_search_limit=max_search_calls,
            )
        )
        normalized["budget_decision"] = decision

        if limit_reached:
            return normalized, ToolResult(
                status="degraded",
                error_message="web_search 已达到本轮联网预算",
                data={
                    "query": query,
                    "sources": [],
                    "result_count": 0,
                    "requested_count": search_budget.requested_count,
                    "actual_count": 0,
                    "context_source_count": 0,
                    "context_source_limit": search_budget.context_source_limit,
                    "search_budget": search_budget.name,
                    "domains": normalized.get("domains") if isinstance(normalized.get("domains"), list) else [],
                    "recency_days": normalized.get("recency_days"),
                    "budget_limited": True,
                    "budget_decision": decision,
                },
            )

        self.web_search_calls += 1
        self.web_search_queries.append(query)
        return normalized, None

    def prepare_url_read_args(
        self,
        args: dict,
    ) -> tuple[dict, ToolResult | None]:
        strategy_config = get_agent_strategy_config()
        network_config = _network_config(strategy_config)
        normalized = dict(args or {})
        max_url_read_calls = _network_int(network_config, "max_url_read_calls", MAX_URL_READ_CALLS)
        if self.url_read_calls >= max_url_read_calls:
            return normalized, ToolResult(
                status="degraded",
                error_message="url_read 已达到本轮联网预算",
                data={
                    "url": normalized.get("url", ""),
                    "budget_limited": True,
                },
            )

        self.url_read_calls += 1
        return normalized, None


def _network_config(strategy_config: dict | None) -> dict:
    return (strategy_config or {}).get("network") or {}


def _network_int(network_config: dict | None, key: str, fallback: int) -> int:
    try:
        return max(0, int((network_config or {}).get(key, fallback)))
    except (TypeError, ValueError):
        return fallback
