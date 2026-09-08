"""按供应商返回顺序整理搜索候选，去重键不改写原始 URL。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import SearchSource
from app.services.source_url_identity import canonicalize_source_url


@dataclass(frozen=True)
class SearchResultForRanking:
    tool_call_id: str
    query: str
    sources: list[SearchSource | dict]
    intent: str | None = None
    search_budget: str | None = None


@dataclass(frozen=True)
class RankedSourceCandidate:
    rank: int
    title: str
    url: str
    domain: str
    query: str
    tool_call_id: str
    source_index: int
    score: int
    priority: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SourceReadDecision:
    candidate: RankedSourceCandidate
    action: str
    reason_code: str


@dataclass(frozen=True)
class SourceSelectionPlan:
    total_source_count: int
    unique_source_count: int
    search_queries: tuple[str, ...]
    candidates: tuple[RankedSourceCandidate, ...]
    recommended: tuple[RankedSourceCandidate, ...]
    low_priority: tuple[RankedSourceCandidate, ...]
    read_decisions: tuple[SourceReadDecision, ...]
    decision_summary: dict[str, int]
    recommended_read_limit: int = 0
    not_recommended_count: int = 0
    read_required: bool = False
    minimum_required_reads: int = 0
    read_required_reason: str = ""


def rank_search_sources(
    search_results: list[SearchResultForRanking],
    *,
    max_recommended: int = 3,
    read_required: bool = False,
    minimum_required_reads: int = 0,
    read_required_reason: str = "",
) -> SourceSelectionPlan:
    """保留旧调用参数以兼容历史消费者；阅读数量和优先级不再由服务端生成。"""
    candidates = []
    seen = set()
    for result in search_results:
        for source_index, source in enumerate(result.sources, 1):
            url = _source_field(source, "url")
            key = canonicalize_source_url(url)
            domain = urlsplit(key).hostname or ""
            if (key or url) in seen:
                continue
            seen.add(key or url)
            candidates.append(
                RankedSourceCandidate(
                    rank=len(candidates) + 1,
                    title=_source_field(source, "title") or url,
                    url=url,
                    domain=domain,
                    query=result.query,
                    tool_call_id=result.tool_call_id,
                    source_index=source_index,
                    score=0,
                    priority="",
                    reasons=(),
                )
            )
    decisions = tuple(SourceReadDecision(candidate, "keep_candidate", "provider_order") for candidate in candidates)
    return SourceSelectionPlan(
        total_source_count=sum(len(result.sources) for result in search_results),
        unique_source_count=len(candidates),
        search_queries=tuple(result.query for result in search_results if result.query),
        candidates=tuple(candidates),
        recommended=(),
        low_priority=(),
        read_decisions=decisions,
        decision_summary={"keep_candidate": len(candidates), "provider_order": len(candidates)},
    )


def format_source_selection_guidance(plan: SourceSelectionPlan) -> str:
    """只陈述候选数量、原始次序和查询归属，不另建引用编号。"""
    if not plan.candidates:
        return ""
    parts = [
        render_runtime_prompt(
            "source_selection.header",
            total_source_count=plan.total_source_count,
            unique_source_count=plan.unique_source_count,
        )
    ]
    for candidate in plan.candidates:
        parts.append(f"- {candidate.title}\n  URL: {candidate.url}\n  Query: {candidate.query}")
    return "\n".join(parts)


def _source_field(source: SearchSource | dict, field_name: str) -> str:
    value = source.get(field_name) if isinstance(source, dict) else getattr(source, field_name, None)
    return str(value or "")
