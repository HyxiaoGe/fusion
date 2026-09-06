"""搜索候选来源排序器。

本模块只做确定性的候选来源评分、去重和解释，不直接触发 url_read。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import SearchSource
from app.services.agent_strategy_config import get_agent_strategy_config

MAX_LOW_PRIORITY_EXAMPLES = 3
TRACKING_QUERY_PARAMS = {
    "_hsenc",
    "_hsmi",
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "msclkid",
    "spm",
    "ttclid",
    "twclid",
    "yclid",
}
STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "news",
    "the",
    "with",
    "发布",
    "新闻",
    "最新",
    "官方",
    "公告",
}
AUTHORITY_MEDIA_DOMAINS = {
    "apnews.com",
    "axios.com",
    "bloomberg.com",
    "cnbc.com",
    "ft.com",
    "nytimes.com",
    "reuters.com",
    "techcrunch.com",
    "theverge.com",
    "venturebeat.com",
    "wired.com",
    "wsj.com",
}
LOW_PRIORITY_DOMAINS = {
    "bilibili.com",
    "douyin.com",
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "threads.com",
    "tiktok.com",
    "twitter.com",
    "weibo.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "zhihu.com",
}
VIDEO_DOMAINS = {"bilibili.com", "douyin.com", "tiktok.com", "youtube.com", "youtu.be"}
FORUM_DOMAINS = {"reddit.com", "threads.com", "twitter.com", "weibo.com", "x.com", "zhihu.com"}


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
    recommended_read_limit: int = 3
    not_recommended_count: int = 0
    read_required: bool = False
    minimum_required_reads: int = 0
    read_required_reason: str = ""


@dataclass(frozen=True)
class _CandidateDraft:
    title: str
    url: str
    canonical_url: str
    domain: str
    query: str
    tool_call_id: str
    source_index: int
    source_order: int
    score: int
    priority: str
    reasons: tuple[str, ...]


def rank_search_sources(
    search_results: list[SearchResultForRanking],
    *,
    max_recommended: int = 3,
    read_required: bool = False,
    minimum_required_reads: int = 0,
    read_required_reason: str = "",
) -> SourceSelectionPlan:
    """对同一轮多个搜索结果做跨搜索去重和深读候选排序。"""
    strategy_config, _meta = get_agent_strategy_config()
    ranker_config = _ranker_config(strategy_config)
    total_source_count = sum(len(result.sources) for result in search_results)
    drafts = _build_candidate_drafts(search_results, ranker_config=ranker_config)
    deduped = _dedupe_candidates(drafts)
    ranked = tuple(
        RankedSourceCandidate(
            rank=index,
            title=draft.title,
            url=draft.canonical_url or draft.url,
            domain=draft.domain,
            query=draft.query,
            tool_call_id=draft.tool_call_id,
            source_index=draft.source_index,
            score=draft.score,
            priority=draft.priority,
            reasons=draft.reasons,
        )
        for index, draft in enumerate(sorted(deduped, key=_sort_candidate), 1)
    )
    recommended_limit = max(0, max_recommended)
    recommended = tuple(candidate for candidate in ranked if candidate.priority != "low")[:recommended_limit]
    low_priority = tuple(candidate for candidate in ranked if candidate.priority == "low")
    read_decisions = _build_read_decisions(ranked, recommended)
    normalized_minimum_reads = 0
    if read_required:
        normalized_minimum_reads = min(len(recommended), max(0, minimum_required_reads))
    return SourceSelectionPlan(
        total_source_count=total_source_count,
        unique_source_count=len(ranked),
        search_queries=tuple(result.query for result in search_results if result.query),
        candidates=ranked,
        recommended=recommended,
        low_priority=low_priority,
        read_decisions=read_decisions,
        decision_summary=_summarize_read_decisions(read_decisions),
        recommended_read_limit=recommended_limit,
        not_recommended_count=max(0, len(ranked) - len(recommended)),
        read_required=read_required and normalized_minimum_reads > 0,
        minimum_required_reads=normalized_minimum_reads,
        read_required_reason=read_required_reason if read_required and normalized_minimum_reads > 0 else "",
    )


def format_source_selection_guidance(plan: SourceSelectionPlan) -> str:
    """生成给 LLM 的本轮搜索候选选择建议。"""
    if not plan.candidates:
        return ""

    parts = [
        render_runtime_prompt(
            "source_selection.header",
            total_source_count=plan.total_source_count,
            unique_source_count=plan.unique_source_count,
        )
    ]
    if plan.search_queries:
        parts.append(render_runtime_prompt("source_selection.search_queries"))
        parts.extend(f"{index}. {query}" for index, query in enumerate(plan.search_queries, 1))

    parts.append(render_runtime_prompt("source_selection.read_limit", limit=plan.recommended_read_limit))
    if plan.read_required:
        parts.append(render_runtime_prompt("source_selection.read_required", count=plan.minimum_required_reads))

    if plan.recommended:
        parts.append(render_runtime_prompt("source_selection.recommended"))
        for candidate in plan.recommended:
            parts.append(_format_candidate_line(candidate))

    if plan.low_priority:
        parts.append(render_runtime_prompt("source_selection.low_priority"))
        for candidate in plan.low_priority[:MAX_LOW_PRIORITY_EXAMPLES]:
            parts.append(_format_candidate_line(candidate))

    if plan.not_recommended_count:
        parts.append(render_runtime_prompt("source_selection.not_recommended", count=plan.not_recommended_count))
        reason_summary = _format_not_recommended_reason_summary(plan.read_decisions)
        if reason_summary:
            parts.append(render_runtime_prompt("source_selection.not_recommended_reasons"))
            parts.extend(reason_summary)

    if plan.read_required:
        parts.append(render_runtime_prompt("source_selection.execution_required"))
    else:
        parts.append(render_runtime_prompt("source_selection.execution_optional"))
    return "\n".join(parts)


def _build_candidate_drafts(
    search_results: list[SearchResultForRanking],
    *,
    ranker_config: dict,
) -> list[_CandidateDraft]:
    drafts: list[_CandidateDraft] = []
    source_order = 0
    for result in search_results:
        for source_index, source in enumerate(result.sources, 1):
            source_order += 1
            drafts.append(
                _score_source(
                    source,
                    result.query,
                    result.tool_call_id,
                    source_index,
                    source_order,
                    ranker_config=ranker_config,
                )
            )
    return drafts


def _score_source(
    source: SearchSource | dict,
    query: str,
    tool_call_id: str,
    source_index: int,
    source_order: int,
    *,
    ranker_config: dict,
) -> _CandidateDraft:
    source_url = _source_field(source, "url")
    source_description = _source_field(source, "description")
    source_content = _source_field(source, "content")
    canonical_url, domain = _canonicalize_url(source_url)
    title = _source_field(source, "title") or source_url
    text = " ".join([title, source_description, source_content, canonical_url or source_url])
    text_lower = text.lower()
    query_terms = _tokenize(query)
    domain_tokens = set(_tokenize(domain.replace(".", " ")))
    weights = _weights(ranker_config)
    score = max(
        0,
        _weight(weights, "source_order_base", 22) - source_index * _weight(weights, "source_order_step", 2),
    )
    reasons: list[str] = []
    is_low_priority = False

    is_official = _is_official_source(domain_tokens, query_terms)
    is_authority_media = _is_authority_media(domain, ranker_config)
    if is_official:
        score += _weight(weights, "official", 38)
        reasons.append("official source")
    if _has_original_signal(text_lower, canonical_url, is_official, is_authority_media):
        score += _weight(weights, "original", 22)
        reasons.append("primary announcement")
    has_specific_original = _has_specific_original_signal(text_lower, canonical_url)
    is_pdf = _is_pdf(canonical_url, title)
    if has_specific_original:
        score += _weight(weights, "specific_original", 18)
        reasons.append("specific primary page")
    if is_official and has_specific_original and not is_pdf and not _is_news_listing(text_lower, canonical_url):
        score += _weight(weights, "official_original", 35)
        reasons.append("official primary source preferred")
    if is_pdf:
        score += _weight(weights, "pdf", 35)
        reasons.append(
            "official PDF or technical report" if "official source" in reasons else "PDF or technical report"
        )
    if is_authority_media:
        score += _weight(weights, "authority_media", 36)
        reasons.append("authoritative media")
    if _is_news_listing(text_lower, canonical_url):
        score -= _weight(weights, "listing_penalty", 28)
        reasons.append("listing page deprioritized")

    relevance_score = _relevance_score(query_terms, text_lower, ranker_config=ranker_config)
    if relevance_score:
        score += relevance_score
        reasons.append("high relevance")

    if _is_video_source(domain, title, ranker_config):
        score -= _weight(weights, "video_penalty", 28)
        reasons.append("video source deprioritized by default")
        is_low_priority = True
    elif _is_forum_source(domain, ranker_config):
        score -= _weight(weights, "forum_penalty", 24)
        reasons.append("social or forum source deprioritized by default")
        is_low_priority = True
    elif domain in _domain_set(ranker_config, "low_priority_domains", LOW_PRIORITY_DOMAINS):
        score -= _weight(weights, "low_priority_penalty", 18)
        reasons.append("low-relevance source deprioritized by default")
        is_low_priority = True

    priority = _priority(score, is_low_priority, ranker_config)
    return _CandidateDraft(
        title=title,
        url=source_url,
        canonical_url=canonical_url,
        domain=domain,
        query=query,
        tool_call_id=tool_call_id,
        source_index=source_index,
        source_order=source_order,
        score=score,
        priority=priority,
        reasons=tuple(dict.fromkeys(reasons or ["standard candidate"])),
    )


def _dedupe_candidates(drafts: list[_CandidateDraft]) -> list[_CandidateDraft]:
    by_url: dict[str, _CandidateDraft] = {}
    for draft in drafts:
        key = draft.canonical_url or draft.url.strip()
        previous = by_url.get(key)
        if previous is None or (draft.score, -draft.source_order) > (previous.score, -previous.source_order):
            by_url[key] = draft
    return list(by_url.values())


def _build_read_decisions(
    candidates: tuple[RankedSourceCandidate, ...],
    recommended: tuple[RankedSourceCandidate, ...],
) -> tuple[SourceReadDecision, ...]:
    recommended_urls = {candidate.url for candidate in recommended}
    decisions: list[SourceReadDecision] = []
    for candidate in candidates:
        if candidate.url in recommended_urls:
            decisions.append(
                SourceReadDecision(
                    candidate=candidate,
                    action="recommend_read",
                    reason_code=_recommended_reason_code(candidate),
                )
            )
            continue
        if candidate.priority == "low":
            decisions.append(
                SourceReadDecision(
                    candidate=candidate,
                    action="deprioritize",
                    reason_code="low_priority_source_type",
                )
            )
            continue
        decisions.append(
            SourceReadDecision(
                candidate=candidate,
                action="keep_candidate",
                reason_code="outside_read_limit",
            )
        )
    return tuple(decisions)


def _recommended_reason_code(candidate: RankedSourceCandidate) -> str:
    reasons = set(candidate.reasons)
    if any("PDF" in reason or "technical report" in reason for reason in reasons):
        return "official_document"
    if "official primary source preferred" in reasons or (
        "official source" in reasons and ("primary announcement" in reasons or "specific primary page" in reasons)
    ):
        return "official_original"
    if "authoritative media" in reasons:
        return "authority_media"
    return "high_relevance"


def _summarize_read_decisions(decisions: tuple[SourceReadDecision, ...]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for decision in decisions:
        counter[decision.action] += 1
        counter[decision.reason_code] += 1
    return dict(counter)


def _format_not_recommended_reason_summary(decisions: tuple[SourceReadDecision, ...]) -> list[str]:
    labels = {
        "low_priority_source_type": "low-priority source type",
        "outside_read_limit": "outside the recommended reading limit",
        "covered_by_recommended_source": "covered by a higher-quality source",
    }
    counter: Counter[str] = Counter(
        decision.reason_code for decision in decisions if decision.action != "recommend_read"
    )
    return [f"- {label}: {counter[reason_code]}" for reason_code, label in labels.items() if counter[reason_code]]


def _source_field(source: SearchSource | dict, field_name: str) -> str:
    if isinstance(source, dict):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)
    return str(value or "")


def _sort_candidate(candidate: _CandidateDraft) -> tuple[int, int]:
    return (-candidate.score, candidate.source_order)


def _format_candidate_line(candidate: RankedSourceCandidate) -> str:
    priority_label = {"high": "high priority", "medium": "medium priority", "low": "low priority"}.get(
        candidate.priority, candidate.priority
    )
    reasons = ", ".join(candidate.reasons)
    return f"- R{candidate.rank} {priority_label} | {candidate.domain or 'unknown'} | {candidate.title}\n  URL: {candidate.url}\n  Reasons: {reasons}"


def _canonicalize_url(url: str) -> tuple[str, str]:
    stripped_url = (url or "").strip()
    if not stripped_url:
        return "", ""
    try:
        parsed = urlsplit(stripped_url)
    except ValueError:
        return stripped_url, ""
    if not parsed.netloc:
        return stripped_url, ""

    scheme = parsed.scheme.lower() or "https"
    domain = _normalize_domain(parsed.hostname or "")
    if not domain:
        return stripped_url, ""
    try:
        port = parsed.port
    except ValueError:
        return stripped_url, domain

    include_port = port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443))
    netloc = f"{domain}:{port}" if include_port else domain
    path = "" if parsed.path == "/" else parsed.path.rstrip("/")
    query = _canonicalize_query(parsed.query)
    return urlunsplit((scheme, netloc, path, query, "")), domain


def _canonicalize_query(query: str) -> str:
    params = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key.startswith("utm_") or normalized_key in TRACKING_QUERY_PARAMS:
            continue
        params.append((key, value))
    params.sort(key=lambda item: (item[0].lower(), item[1]))
    return urlencode(params, doseq=True)


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().rstrip(".").lower()
    while normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


def _tokenize(text: str) -> set[str]:
    tokens = {token.casefold() for token in re.findall(r"[\w.-]+", text or "", flags=re.UNICODE)}
    return {token for token in tokens if len(token) >= 3 and not token.isdigit() and token not in STOP_WORDS}


def _is_official_source(domain_tokens: set[str], query_terms: set[str]) -> bool:
    return bool(domain_tokens & {term for term in query_terms if len(term) >= 4})


def _has_original_signal(text_lower: str, canonical_url: str, is_official: bool, is_authority_media: bool) -> bool:
    original_keywords = (
        "announcement",
        "announcing",
        "changelog",
        "docs",
        "documentation",
        "newsroom",
        "previewing",
        "press",
        "release",
        "released",
        "system card",
        "公告",
        "官方",
        "新闻中心",
    )
    if is_official:
        return any(keyword in text_lower or keyword in canonical_url.lower() for keyword in original_keywords)
    if is_authority_media:
        return "release" in text_lower or "released" in text_lower or "reports" in text_lower
    return False


def _has_specific_original_signal(text_lower: str, canonical_url: str) -> bool:
    specific_keywords = ("previewing", "/index/", "/blog/", "/docs/", "system-card", "system card")
    return any(keyword in text_lower or keyword in canonical_url.lower() for keyword in specific_keywords)


def _is_news_listing(text_lower: str, canonical_url: str) -> bool:
    listing_keywords = ("company-announcements", "news/company", "新闻中心", "最新动态", "newsroom")
    return any(keyword in text_lower or keyword in canonical_url.lower() for keyword in listing_keywords)


def _is_pdf(url: str, title: str) -> bool:
    lowered_url = (url or "").lower()
    lowered_title = (title or "").lower()
    return lowered_url.endswith(".pdf") or "[pdf]" in lowered_title or "system card" in lowered_title


def _is_authority_media(domain: str, ranker_config: dict) -> bool:
    return domain in _domain_set(ranker_config, "authority_media_domains", AUTHORITY_MEDIA_DOMAINS)


def _is_video_source(domain: str, title: str, ranker_config: dict) -> bool:
    title_lower = (title or "").lower()
    return (
        domain in _domain_set(ranker_config, "video_domains", VIDEO_DOMAINS)
        or "youtube" in title_lower
        or "视频" in title_lower
        or "video" in title_lower
    )


def _is_forum_source(domain: str, ranker_config: dict) -> bool:
    return domain in _domain_set(ranker_config, "forum_domains", FORUM_DOMAINS)


def _relevance_score(query_terms: set[str], text_lower: str, *, ranker_config: dict) -> int:
    if not query_terms:
        return 0
    matched = sum(1 for term in query_terms if term in text_lower)
    weights = _weights(ranker_config)
    return min(_weight(weights, "relevance_max", 18), matched * _weight(weights, "relevance_per_term", 4))


def _priority(score: int, is_low_priority: bool, ranker_config: dict) -> str:
    if is_low_priority:
        return "low"
    thresholds = ranker_config.get("priority_thresholds") or {}
    if score >= _weight(thresholds, "high", 60):
        return "high"
    if score >= _weight(thresholds, "medium", 30):
        return "medium"
    return "low"


def _ranker_config(strategy_config: dict | None) -> dict:
    return (strategy_config or {}).get("source_ranker") or {}


def _weights(ranker_config: dict) -> dict:
    return ranker_config.get("weights") or {}


def _weight(weights: dict, key: str, fallback: int) -> int:
    try:
        return int(weights.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _domain_set(ranker_config: dict, key: str, fallback: set[str]) -> set[str]:
    configured = ranker_config.get(key)
    if isinstance(configured, list):
        return {str(domain).strip().lower() for domain in configured if str(domain).strip()}
    return set(fallback)
