"""
WebSearchHandler — 网络搜索工具处理器
从 stream_handler.py 提取，行为保持不变
"""

import time
from typing import List, Optional

from app.ai.prompts.agent_loop import (
    SEARCH_CONTEXT_FOLLOW_UP_RULES,
    SEARCH_CONTEXT_OPENING,
)
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import SearchBlock, SearchSource, SearchSourceSummary, SourceReference
from app.services.agent_strategy_config import get_agent_strategy_config
from app.services.external.search_client import search_web
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.source_url_identity import canonicalize_source_url
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

MAX_CONTEXT_SOURCES = 10


class WebSearchHandler(BaseToolHandler):
    supports_run_level_citations = True

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def sse_event_prefix(self) -> str:
        return "search"

    async def execute(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        context_source_limit = _normalize_context_source_limit(args.get("context_source_limit"))
        search_budget = args.get("search_budget")
        if not query:
            return ToolResult(
                status="degraded",
                error_message="query 为空",
                data={
                    "query": query,
                    "sources": [],
                    "result_count": 0,
                    "requested_count": args.get("count", 10),
                    "actual_count": 0,
                    "context_source_count": 0,
                    "context_source_limit": context_source_limit,
                    "search_budget": search_budget,
                    "intent": args.get("intent"),
                    "domains": args.get("domains", []),
                    "recency_days": args.get("recency_days"),
                    "budget_limited": bool(args.get("budget_limited", False)),
                },
            )

        requested_count = args.get("count", 10)
        domains = args.get("domains") or []
        recency_days = args.get("recency_days")
        intent = args.get("intent")
        start = time.monotonic()
        try:
            raw_sources = await search_web(query, count=requested_count, domains=domains, recency_days=recency_days)
            duration_ms = int((time.monotonic() - start) * 1000)

            if not raw_sources:
                return ToolResult(
                    status="degraded",
                    duration_ms=duration_ms,
                    error_message="搜索返回空结果",
                    data={
                        "query": query,
                        "sources": [],
                        "result_count": 0,
                        "requested_count": requested_count,
                        "actual_count": 0,
                        "context_source_count": 0,
                        "context_source_limit": context_source_limit,
                        "search_budget": search_budget,
                        "intent": intent,
                        "domains": domains,
                        "recency_days": recency_days,
                        "budget_limited": False,
                    },
                )

            provider_metadata = _extract_provider_metadata(raw_sources)
            sources = _post_process_sources(raw_sources, intent=intent, domains=domains)
            context_source_count = min(len(sources), context_source_limit)
            return ToolResult(
                status="success",
                duration_ms=duration_ms,
                data={
                    "query": query,
                    "sources": sources,
                    "result_count": len(sources),
                    "requested_count": requested_count,
                    "actual_count": len(raw_sources),
                    "context_source_count": context_source_count,
                    "context_source_limit": context_source_limit,
                    "search_budget": search_budget,
                    "intent": intent,
                    "domains": domains,
                    "recency_days": recency_days,
                    "budget_limited": False,
                    **provider_metadata,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 上游异常文本可能含凭据，禁止回显或落日志
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "联网搜索调用异常: error_type=%s",
                type(exc).__name__,
            )
            return ToolResult(
                status="failed",
                duration_ms=duration_ms,
                error_message="搜索服务暂时不可用",
                data={
                    "error_code": "search_unavailable",
                    "retryable": True,
                    "query": query,
                    "sources": [],
                    "result_count": 0,
                    "requested_count": requested_count,
                    "actual_count": 0,
                    "context_source_count": 0,
                    "context_source_limit": context_source_limit,
                    "search_budget": search_budget,
                    "intent": intent,
                    "domains": domains,
                    "recency_days": recency_days,
                    "budget_limited": False,
                },
            )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> SearchBlock | None:
        sources: List[SearchSource] = result.data.get("sources", [])
        source_refs = [
            SourceReference(
                kind="search",
                title=s.title,
                url=s.url,
                favicon=s.favicon,
                status=result.status,
                tool_call_log_id=log_id,
                error_message=result.error_message,
            )
            for s in sources
        ]
        return SearchBlock(
            type="search",
            id=block_id,
            query=result.data.get("query", ""),
            tool_call_log_id=log_id,
            sources=[
                SearchSourceSummary(
                    title=s.title,
                    url=s.url,
                    favicon=s.favicon,
                )
                for s in sources
            ],
            status=result.status,
            error_message=result.error_message,
            source_count=len(source_refs),
            source_refs=source_refs,
            requested_provider=result.data.get("requested_provider"),
            result_provider=result.data.get("result_provider"),
            fallback_used=bool(result.data.get("fallback_used", False)),
            provider_chain=result.data.get("provider_chain", []),
            requested_count=result.data.get("requested_count"),
            actual_count=result.data.get("actual_count"),
            context_source_count=result.data.get("context_source_count"),
            context_source_limit=result.data.get("context_source_limit"),
            search_budget=result.data.get("search_budget"),
            intent=result.data.get("intent"),
            domains=result.data.get("domains", []),
            recency_days=result.data.get("recency_days"),
            budget_limited=bool(result.data.get("budget_limited", False)),
        )

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        query_context = render_runtime_prompt("tool_handlers.search_query", query=result.data.get("query", ""))
        sources: List[SearchSource] = result.data.get("sources", [])
        if not sources:
            return f"{query_context}\n{render_runtime_prompt('tool_handlers.search_unavailable')}"

        parts = [query_context, SEARCH_CONTEXT_OPENING, render_runtime_prompt("source_context.rules")]

        context_source_limit = _normalize_context_source_limit(result.data.get("context_source_limit"))
        context_sources = sources[:context_source_limit]
        if len(sources) > len(context_sources):
            parts.append(
                render_runtime_prompt(
                    "tool_handlers.search_truncated",
                    source_count=len(sources),
                    context_count=len(context_sources),
                )
            )

        for source_index, source in enumerate(context_sources):
            citation_number = _citation_number(citation_numbers, source_index)
            parts.append(f"[{citation_number}] {source.title}")
            parts.append(f"    Source: {source.url}")
            content = source.content or source.description
            parts.append(
                format_untrusted_source_context(
                    UntrustedSourceContext(
                        source_id=str(citation_number),
                        source_type="search",
                        title=source.title,
                        url=source.url,
                        content=content,
                        provider="search-service",
                        published_at=source.published_at,
                        site_name=source.site_name,
                    ),
                    max_chars=1000,
                    include_rules=False,
                )
            )
            parts.append("")

        for source_index, source in enumerate(sources[len(context_sources) :], start=len(context_sources)):
            citation_number = _citation_number(citation_numbers, source_index)
            parts.append(f"[{citation_number}] {source.title}")
            parts.append(
                format_untrusted_source_context(
                    UntrustedSourceContext(
                        source_id=str(citation_number),
                        source_type="search",
                        title=source.title,
                        url=source.url,
                        content="",
                        provider="search-service",
                        published_at=source.published_at,
                        site_name=source.site_name,
                    ),
                    max_chars=300,
                    include_rules=False,
                )
            )

        parts.append("Notes:")
        parts.extend(f"- {rule}" for rule in SEARCH_CONTEXT_FOLLOW_UP_RULES)

        return "\n".join(parts)

    def _build_result_summary(self, result: ToolResult) -> dict:
        """搜索结果轻量摘要：命中数 + 首条标题/favicon。

        emitter.tool_call_completed 内部还会经 cap_and_truncate(1024) 兜底。
        """
        data = result.data or {}
        if result.status != "success":
            return {"kind": "search", "truncated": False}
        sources = data.get("sources") or []
        first = sources[0] if sources else None
        return {
            "kind": "search",
            "title": getattr(first, "title", "") if first else "",
            "count": len(sources),
            "favicon": getattr(first, "favicon", None) if first else None,
            "result_provider": data.get("result_provider"),
            "truncated": False,
        }


def _extract_provider_metadata(sources: List[SearchSource]) -> dict:
    first = next((source for source in sources if source.result_provider or source.requested_provider), None)
    if not first:
        return {}

    return {
        "requested_provider": first.requested_provider,
        "result_provider": first.result_provider,
        "fallback_used": first.fallback_used,
        "provider_chain": first.provider_chain,
    }


def _normalize_context_source_limit(value) -> int:
    tool_context = _tool_context_config()
    max_context_sources = _tool_context_int(tool_context, "max_context_sources", MAX_CONTEXT_SOURCES)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = max_context_sources
    return max(1, min(max_context_sources, parsed))


def _citation_number(citation_numbers: list[int] | None, source_index: int) -> int:
    if citation_numbers is not None and source_index < len(citation_numbers):
        candidate = citation_numbers[source_index]
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return source_index + 1


def _post_process_sources(sources: List[SearchSource], intent: Optional[str], domains: list[str]) -> List[SearchSource]:
    # 只用规范化键去重；原链接及 provider 顺序必须用于展示和后续读取。
    seen_urls: set[str] = set()
    processed: List[SearchSource] = []
    for source in sources:
        key = canonicalize_source_url(source.url)
        url_key = key or source.url.strip()
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        processed.append(source)
    return processed


def _tool_context_config() -> dict:
    strategy_config, _meta = get_agent_strategy_config()
    return strategy_config.get("tool_context") or {}


def _tool_context_int(tool_context: dict, key: str, fallback: int) -> int:
    try:
        return max(1, int(tool_context.get(key, fallback)))
    except (TypeError, ValueError):
        return fallback
