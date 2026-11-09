"""
web_search：经 agent-tools 网关调用搜索服务的 search_web。

参数检查、时效换档、限定网站过滤、同一批去重、摘要截断、给模型的说明和失败话术都在服务里
（agent-tools services/search）。这里只做 Fusion 自己的事：
- 工具在 Fusion 叫 web_search；说明用服务原文，后面接 Fusion 的搜索策略（ai_tools.web_search_product_notes）。
  服务目录还没读到时用同名的最小定义，免得模型以为没有搜索、凭记忆回答；调用照常走网关。
- 每轮次数上限和进上下文的条数（network_budget 填进参数，转发前去掉）。
- 结果编成全局引用编号 [n] 的不可信来源上下文；来源卡和站点图标（服务放在 meta.favicons）。
"""

from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.ai.prompts.agent_loop import SEARCH_CONTEXT_OPENING
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import SearchBlock, SearchSource, SearchSourceSummary, SourceReference
from app.services.agent_strategy_config import get_agent_strategy_config
from app.services.external.agent_tools_mcp import (
    GatewayToolCatalog,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
    gateway_tool_catalog,
)
from app.services.mcp.tool_contract import sanitize_tool_schema_for_model, validate_tool_arguments
from app.services.search_budget import MAX_CONTEXT_SOURCES
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

WEB_SEARCH_TOOL_NAME = "web_search"
SEARCH_SERVICE = "search"
SEARCH_REMOTE_TOOL = "search_web"
_CALL_SCENE = "web_search"
# 服务整次搜索 25 秒时限（含备用链），留出网关转发的余量；执行器单次工具 30 秒。
_CALL_TIMEOUT_SECONDS = 28.0
# network_budget 填进参数给 handler 用的字段；服务的 schema 不收别的字段，转发前去掉。
_FUSION_ARGUMENT_KEYS = frozenset({"context_source_limit", "search_budget"})
# 网关 429 是本产品当天的调用额度用完了：再搜也一样，按额度用完告诉模型。
_GATEWAY_LIMIT_CODE = "rate_limited"
QUOTA_EXHAUSTED_CODE = "quota_exhausted"
# 每条搜索来源进入模型上下文的字数上限（服务的 snippet 也是 1000 字）；评测裁判按同一上限核对
SOURCE_CONTEXT_CHARS = 1000
_CHINA_TZ = timezone(timedelta(hours=8))
_MINIMAL_PARAMETERS = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def build_web_search_tool(catalog: GatewayToolCatalog | None = None) -> dict:
    """每个 Run 装配一次：服务说明 + Fusion 的搜索策略（当前年份动态注入）。"""

    product_notes = render_runtime_prompt("ai_tools.web_search_product_notes", year=datetime.now(_CHINA_TZ).year)
    spec = _search_spec(catalog)
    if spec is not None:
        return build_gateway_tool_definition(spec, name=WEB_SEARCH_TOOL_NAME, product_notes=product_notes)
    return {
        "type": "function",
        "function": {
            "name": WEB_SEARCH_TOOL_NAME,
            "description": f"{render_runtime_prompt('ai_tools.web_search_minimal_description')}\n\n{product_notes}",
            "parameters": copy.deepcopy(_MINIMAL_PARAMETERS),
        },
    }


def _search_spec(catalog: GatewayToolCatalog | None = None) -> GatewayToolSpec | None:
    return (catalog or gateway_tool_catalog()).spec(SEARCH_SERVICE, SEARCH_REMOTE_TOOL)


def _announced_parameters() -> dict[str, Any]:
    spec = _search_spec()
    if spec is None:
        return copy.deepcopy(_MINIMAL_PARAMETERS)
    return sanitize_tool_schema_for_model(spec.input_schema, keep_descriptions=True)


class WebSearchHandler(BaseToolHandler):
    supports_run_level_citations = True
    # 服务有自己的备用链和总时限；失败后值不值得再搜由服务的说明告诉模型，这里不自动重试
    # （额度用完时自动重试只会再失败一次）。
    supports_automatic_retry = False

    @property
    def tool_name(self) -> str:
        return WEB_SEARCH_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "search"

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """只按公告的 schema 校验形状；取值范围（条数、天数、域名）由服务检查并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(_announced_parameters(), args)

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        query = args.get("query") if isinstance(args.get("query"), str) else ""
        context_source_limit = _normalize_context_source_limit(args.get("context_source_limit"))
        domains = args.get("domains") if isinstance(args.get("domains"), list) else []
        count = args.get("count")
        data: dict[str, Any] = {
            "query": query,
            "sources": [],
            "result_count": 0,
            "requested_count": count if isinstance(count, int) and not isinstance(count, bool) else None,
            "actual_count": 0,
            "context_source_count": 0,
            "context_source_limit": context_source_limit,
            "search_budget": args.get("search_budget"),
            "domains": domains,
            "recency_days": args.get("recency_days"),
            "budget_limited": False,
        }
        start = time.monotonic()
        outcome = await call_gateway_tool(
            SEARCH_SERVICE,
            SEARCH_REMOTE_TOOL,
            {key: value for key, value in args.items() if key not in _FUSION_ARGUMENT_KEYS},
            scene=_CALL_SCENE,
            min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        if not outcome.ok:
            error_code = outcome.error_code or "tool_error"
            if not outcome.model_text:
                logger.warning("搜索服务没有答复: error_code=%s", error_code)
                if error_code == _GATEWAY_LIMIT_CODE:
                    error_code = QUOTA_EXHAUSTED_CODE
            quota_exhausted = error_code == QUOTA_EXHAUSTED_CODE
            return ToolResult(
                status="failed",
                duration_ms=duration_ms,
                error_message="今日搜索额度已用完" if quota_exhausted else "搜索服务暂时不可用",
                data={
                    **data,
                    "error_code": error_code,
                    "retryable": outcome.error.get("retryable") is True,
                    "model_text": outcome.model_text,
                },
            )

        result = outcome.data
        results = [item for item in result.get("results") or [] if isinstance(item, dict)]
        raw_meta = result.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        sources = [_search_source(item, meta) for item in results if _usable_result(item)]
        data.update(
            {
                "query": result.get("query") if isinstance(result.get("query"), str) else query,
                "sources": sources,
                "result_count": len(sources),
                "actual_count": len(results),
                "context_source_count": min(len(sources), context_source_limit),
                "recency_window_days": result.get("recency_window_days"),
                "time_filter_relaxed": result.get("time_filter_relaxed") is True,
                **_provider_metadata(meta),
            }
        )
        if isinstance(result.get("domains"), list):
            data["domains"] = result["domains"]
        if not sources:
            return ToolResult(status="degraded", duration_ms=duration_ms, error_message="搜索返回空结果", data=data)
        return ToolResult(status="success", duration_ms=duration_ms, data=data)

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> SearchBlock | None:
        sources: list[SearchSource] = result.data.get("sources", [])
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
            sources=[SearchSourceSummary(title=s.title, url=s.url, favicon=s.favicon) for s in sources],
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
        data = result.data or {}
        query_context = render_runtime_prompt("tool_handlers.search_query", query=data.get("query", ""))
        if data.get("budget_limited"):
            return f"{query_context}\n{render_runtime_prompt('tool_handlers.search_budget_exhausted')}"
        if result.status == "failed":
            # 服务答了就用服务给模型的说明（参数错、额度用完、可否再试）；网关或网络出错时用 Fusion 的说明。
            model_text = data.get("model_text")
            if not isinstance(model_text, str) or not model_text:
                key = (
                    "search_quota_exhausted" if data.get("error_code") == QUOTA_EXHAUSTED_CODE else "search_unavailable"
                )
                model_text = render_runtime_prompt(f"tool_handlers.{key}")
            return f"{query_context}\n{model_text}"

        parts = [query_context]
        scope = render_runtime_prompt(
            "tool_handlers.search_scope",
            recency_window_days=data.get("recency_window_days"),
            time_filter_relaxed=data.get("time_filter_relaxed") is True,
            domains=", ".join(str(domain) for domain in data.get("domains") or []),
        ).strip()
        if scope:
            parts.append(scope)
        sources: list[SearchSource] = data.get("sources", [])
        if not sources:
            parts.append(render_runtime_prompt("tool_handlers.search_no_results"))
            return "\n".join(parts)

        parts.extend([SEARCH_CONTEXT_OPENING, render_runtime_prompt("source_context.rules")])
        context_source_limit = _normalize_context_source_limit(data.get("context_source_limit"))
        context_sources = sources[:context_source_limit]
        if len(sources) > len(context_sources):
            parts.append(
                render_runtime_prompt(
                    "tool_handlers.search_truncated",
                    source_count=len(sources),
                    context_count=len(context_sources),
                )
            )
        for source_index, source in enumerate(sources):
            with_snippet = source_index < len(context_sources)
            citation_number = _citation_number(citation_numbers, source_index)
            # 标题和网址都是外部内容，只放在包装里（转义过）；包装外只有引用编号。
            parts.append(f"[{citation_number}]")
            parts.append(
                format_untrusted_source_context(
                    UntrustedSourceContext(
                        source_id=str(citation_number),
                        source_type="search",
                        title=source.title,
                        url=source.url,
                        content=(source.content or source.description) if with_snippet else "",
                        provider="search-service",
                        published_at=source.published_at,
                        site_name=source.site_name,
                    ),
                    max_chars=SOURCE_CONTEXT_CHARS,
                    include_rules=False,
                )
            )
            if with_snippet:
                parts.append("")
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


def _usable_result(item: dict[str, Any]) -> bool:
    return isinstance(item.get("url"), str) and bool(item["url"].strip())


def _search_source(item: dict[str, Any], meta: dict[str, Any]) -> SearchSource:
    url = item["url"]
    favicons = meta.get("favicons")
    favicon = favicons.get(url) if isinstance(favicons, dict) else None
    title, snippet, published_at = item.get("title"), item.get("snippet"), item.get("published_at")
    return SearchSource(
        title=title if isinstance(title, str) else "",
        url=url,
        description=snippet if isinstance(snippet, str) else "",
        published_at=published_at if isinstance(published_at, str) else None,
        favicon=favicon if isinstance(favicon, str) else None,
    )


def _provider_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    chain = [name for name in meta.get("provider_chain") or [] if isinstance(name, str)]
    provider = meta.get("provider") if isinstance(meta.get("provider"), str) else None
    if not chain and provider is None:
        return {}
    return {
        "requested_provider": chain[0] if chain else provider,
        "result_provider": provider,
        "fallback_used": meta.get("fallback_used") is True,
        "provider_chain": chain,
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


def _tool_context_config() -> dict:
    return get_agent_strategy_config().get("tool_context") or {}


def _tool_context_int(tool_context: dict, key: str, fallback: int) -> int:
    try:
        return max(1, int(tool_context.get(key, fallback)))
    except (TypeError, ValueError):
        return fallback
