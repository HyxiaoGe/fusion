"""单轮联网工具预算与参数归一化。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from app.services.agent_strategy_config import get_agent_strategy_config
from app.services.search_budget import (
    SearchBudgetDecision,
    derive_search_budget,
    resolve_search_intent,
)
from app.services.source_evidence_ledger import canonicalize_evidence_url
from app.services.tool_handlers.base import ToolResult

MAX_SEARCH_CALLS = 40
MAX_URL_READ_CALLS = 100
MAX_DOMAINS = 5
MIN_RECENCY_DAYS = 1
MAX_RECENCY_DAYS = 365

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass
class NetworkToolBudget:
    """限制一次 assistant run 内的联网工具调用次数。"""

    profile: str = "standard"
    require_distinct_read_urls: bool = False
    web_search_calls: int = 0
    url_read_calls: int = 0
    web_search_queries: list[str] = field(default_factory=list)
    attempted_read_urls: set[str] = field(default_factory=set)
    read_url_plan_items: dict[str, str] = field(default_factory=dict)

    def prepare_web_search_args(self, args: dict) -> tuple[dict, ToolResult | None]:
        strategy_config, _meta = get_agent_strategy_config()
        network_config = _network_config(strategy_config)
        normalized = dict(args or {})

        query = str(normalized.get("query") or "")
        intent = resolve_search_intent(normalized.get("intent"), query, strategy_config=strategy_config)
        if intent:
            normalized["intent"] = intent
        else:
            normalized.pop("intent", None)

        max_domains = _network_int(network_config, "max_domains", MAX_DOMAINS)
        domains = _normalize_domains(normalized.get("domains"), max_domains=max_domains)
        if domains:
            normalized["domains"] = domains
        else:
            normalized.pop("domains", None)

        # 仅保留全局调用上限；旧计划次数不会将互补查询判为“已收敛”。
        planned_search_limit = _network_int(network_config, "max_search_calls", MAX_SEARCH_CALLS)
        previous_query_count = len(self.web_search_queries)

        search_budget = derive_search_budget(intent, requested_count=normalized.get("count"))
        if normalized.get("recency_days") is not None:
            normalized["recency_days"] = _clamp_int(
                normalized.get("recency_days"),
                _network_int(network_config, "min_recency_days", MIN_RECENCY_DAYS),
                _network_int(network_config, "min_recency_days", MIN_RECENCY_DAYS),
                _network_int(network_config, "max_recency_days", MAX_RECENCY_DAYS),
            )
        normalized["count"] = search_budget.requested_count
        normalized["context_source_limit"] = search_budget.context_source_limit
        normalized["search_budget"] = search_budget.name
        max_search_calls = _network_int(network_config, "max_search_calls", MAX_SEARCH_CALLS)
        decision = _search_budget_decision(
            query=query,
            intent=intent,
            action="execute",
            budget_name=normalized["search_budget"],
            requested_count=normalized["count"],
            context_source_limit=normalized["context_source_limit"],
            reason_code=_allowed_search_reason_code(previous_query_count),
            previous_query_count=previous_query_count,
            planned_search_limit=planned_search_limit,
        )
        normalized["budget_decision"] = decision

        if self.web_search_calls >= max_search_calls:
            decision = _search_budget_decision(
                query=str(normalized.get("query") or ""),
                intent=normalized.get("intent"),
                action="limit_budget",
                budget_name=str(normalized.get("search_budget") or search_budget.name),
                requested_count=normalized.get("count", search_budget.requested_count),
                context_source_limit=normalized.get("context_source_limit", search_budget.context_source_limit),
                reason_code="hard_search_limit_reached",
                previous_query_count=previous_query_count,
                planned_search_limit=planned_search_limit,
            )
            normalized["budget_decision"] = decision
            return normalized, ToolResult(
                status="degraded",
                error_message="web_search 已达到本轮联网预算",
                data={
                    "query": normalized.get("query", ""),
                    "sources": [],
                    "result_count": 0,
                    "requested_count": normalized.get("count", search_budget.requested_count),
                    "actual_count": 0,
                    "context_source_count": 0,
                    "context_source_limit": normalized.get(
                        "context_source_limit",
                        search_budget.context_source_limit,
                    ),
                    "search_budget": normalized.get("search_budget", search_budget.name),
                    "intent": normalized.get("intent"),
                    "domains": normalized.get("domains", []),
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
        *,
        plan_item_id: str | None = None,
    ) -> tuple[dict, ToolResult | None]:
        strategy_config, _meta = get_agent_strategy_config()
        network_config = _network_config(strategy_config)
        normalized = dict(args or {})
        max_url_read_calls = _network_int(network_config, "max_url_read_calls", MAX_URL_READ_CALLS)
        if self.url_read_calls >= max_url_read_calls:
            return normalized, ToolResult(
                status="degraded",
                error_message="url_read 已达到本轮联网预算",
                data={
                    "url": normalized.get("url", ""),
                    "reason": normalized.get("reason"),
                    "budget_limited": True,
                },
            )

        canonical_url = canonicalize_evidence_url(str(normalized.get("url") or ""))
        existing_owner = self.read_url_plan_items.get(canonical_url) if canonical_url else None
        if (
            self.require_distinct_read_urls
            and canonical_url
            and canonical_url in self.attempted_read_urls
            and (not plan_item_id or existing_owner != plan_item_id)
        ):
            return normalized, ToolResult(
                status="degraded",
                error_message="该来源已由其他核验步骤读取，请改用不同来源",
                data={
                    "url": normalized.get("url", ""),
                    "reason": normalized.get("reason"),
                    "budget_limited": False,
                    "duplicate_read_source": True,
                    "error_code": "duplicate_read_source",
                    "degraded_reason": "duplicate_read_source",
                    "retryable": True,
                },
            )

        self.url_read_calls += 1
        if self.require_distinct_read_urls and canonical_url:
            self.attempted_read_urls.add(canonical_url)
            if plan_item_id:
                self.read_url_plan_items.setdefault(canonical_url, plan_item_id)
        return normalized, None

    def record_tool_results(self, results: list, *, source_plan=None) -> None:
        """回填本轮工具执行结果，供下一次预算决策使用。"""

        for record in results or []:
            result = getattr(record, "result", None)
            if getattr(record, "tool_name", "") == "url_read" and result is not None:
                self._record_url_read_result(record, result)

    def _record_url_read_result(self, record, result) -> None:
        url = _record_url(record, result)
        canonical_url = canonicalize_evidence_url(url)
        if canonical_url:
            self.attempted_read_urls.add(canonical_url)
            plan_item_id = getattr(record, "tool_call", {}).get("plan_item_id")
            if isinstance(plan_item_id, str) and plan_item_id:
                self.read_url_plan_items.setdefault(canonical_url, plan_item_id)


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_domains(value, *, max_domains: int = MAX_DOMAINS) -> list[str]:
    if not isinstance(value, list):
        return []

    domains: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        domain = _extract_domain(item)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
        if len(domains) >= max_domains:
            break
    return domains


def _extract_domain(value: str) -> str | None:
    raw = value.strip().lower()
    if not raw:
        return None
    if any(char in raw for char in ("://", "/", "?", "#", ":", "*")):
        return None
    if raw.startswith("www."):
        raw = raw[4:]
    if _DOMAIN_RE.match(raw):
        return raw
    return None


def _allowed_search_reason_code(previous_query_count: int) -> str:
    if previous_query_count > 0:
        return "complementary_search"
    return "initial_search"


def _search_budget_decision(
    *,
    query: str,
    intent: str | None,
    action: str,
    budget_name: str,
    requested_count: int,
    context_source_limit: int,
    reason_code: str,
    previous_query_count: int,
    planned_search_limit: int,
) -> dict:
    return asdict(
        SearchBudgetDecision(
            query=query,
            intent=intent,
            action=action,
            budget_name=budget_name,
            requested_count=requested_count,
            context_source_limit=context_source_limit,
            reason_code=reason_code,
            previous_query_count=previous_query_count,
            planned_search_limit=planned_search_limit,
        )
    )


def _network_config(strategy_config: dict | None) -> dict:
    return (strategy_config or {}).get("network") or {}


def _network_int(network_config: dict | None, key: str, fallback: int) -> int:
    try:
        return max(0, int((network_config or {}).get(key, fallback)))
    except (TypeError, ValueError):
        return fallback


def _record_url(record, result) -> str:
    data = getattr(result, "data", None) or {}
    url = data.get("url") if isinstance(data, dict) else ""
    if url:
        return str(url)
    raw_arguments = getattr(record, "tool_call", {}).get("arguments", {})
    if isinstance(raw_arguments, dict):
        return str(raw_arguments.get("url") or "")
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, dict):
            return str(parsed.get("url") or "")
    return ""
