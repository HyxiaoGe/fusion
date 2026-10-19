"""本 run 联网取证的有界安全证据工作集，收尾时注入读取状态清单与来源摘要。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.security.url_policy import MAX_URL_LENGTH
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.source_evidence_ledger import canonicalize_evidence_url, stable_web_evidence_id

MAX_RESEARCH_SOURCES = 12
MAX_RESEARCH_SOURCE_CONTEXT_CHARS = 360
MAX_RESEARCH_SOURCE_URL_CHARS = MAX_URL_LENGTH
_SAFE_EVIDENCE_ID_PATTERN = re.compile(r"^ev-[A-Za-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class ResearchSource:
    evidence_id: str
    citation_index: int
    title: str
    url: str
    kind: str
    summary: str = ""
    key_findings: tuple[str, ...] = ()

    @property
    def url_key(self) -> str:
        """读页状态比较使用完整身份键，恢复链接继续使用原始 URL。"""
        return canonicalize_evidence_url(self.url)


@dataclass
class ResearchEvidenceWorkset:
    """仅保存本 run 新产生的安全来源投影，不接收原始网页或 MCP payload。"""

    sources: dict[str, ResearchSource] = field(default_factory=dict)
    successful_read_urls: set[str] = field(default_factory=set)
    attempted_read_urls: set[str] = field(default_factory=set)
    processed_block_ids: set[str] = field(default_factory=set)

    def record_content_blocks(
        self,
        content_blocks: list[Any],
        *,
        summaries: dict[str, tuple[str, list[str]]] | None = None,
    ) -> None:
        safe_summaries = summaries or {}
        for block in content_blocks:
            block_id = str(_value(block, "id") or "")
            if block_id and block_id in self.processed_block_ids:
                continue
            if block_id:
                self.processed_block_ids.add(block_id)

            block_type = _value(block, "type")
            block_status = _value(block, "status") or "success"
            refs = _value(block, "source_refs")
            if block_type == "url_read":
                attempted_urls = [
                    _value(block, "url"),
                    *(_value(ref, "url") for ref in refs or []),
                ]
                for attempted_url in attempted_urls:
                    canonical_url = canonicalize_evidence_url(str(attempted_url or ""))
                    if canonical_url:
                        self.attempted_read_urls.add(canonical_url)
            if block_status != "success" or not isinstance(refs, list):
                continue
            for ref in refs:
                if (_value(ref, "status") or "success") != "success":
                    continue
                source = _research_source_from_ref(ref, safe_summaries)
                if source is None:
                    continue
                self.sources[source.evidence_id] = source
                if block_type == "url_read" or source.kind == "url_read":
                    self.successful_read_urls.add(source.url_key)

            self._cap_sources()

    def _cap_sources(self) -> None:
        if len(self.sources) <= MAX_RESEARCH_SOURCES:
            return
        read_sources = sorted(
            (source for source in self.sources.values() if source.url_key in self.successful_read_urls),
            key=lambda source: (source.citation_index, source.evidence_id),
        )
        candidates = sorted(
            (
                source
                for source in self.sources.values()
                if source.url_key not in self.successful_read_urls and source.url_key not in self.attempted_read_urls
            ),
            key=lambda source: (source.citation_index, source.evidence_id),
        )
        failed_sources = sorted(
            (
                source
                for source in self.sources.values()
                if source.url_key not in self.successful_read_urls and source.url_key in self.attempted_read_urls
            ),
            key=lambda source: (source.citation_index, source.evidence_id),
        )
        kept = [*read_sources, *candidates, *failed_sources]
        self.sources = {source.evidence_id: source for source in kept[:MAX_RESEARCH_SOURCES]}


def build_research_workset_prompt(
    workset: ResearchEvidenceWorkset,
    *,
    unattempted_request_count: int = 0,
) -> str:
    # 未发起读取的目标必须能单独把清单撑起来：#127 的首个样本就是 url_read 一次都
    # 没发起，此时 sources 为空，早退会让模型完全看不到「有目标没读」这个事实。
    if not workset.sources and unattempted_request_count <= 0:
        return ""
    lines = [render_runtime_prompt("research.workset_header")]
    for source in sorted(
        workset.sources.values(),
        key=lambda item: (item.citation_index, item.evidence_id),
    ):
        is_read = source.url_key in workset.successful_read_urls
        is_failed = source.url_key in workset.attempted_read_urls and not is_read
        source_status = "read_success" if is_read else "read_failed" if is_failed else "candidate"
        evidence_id = (
            source.evidence_id
            if _SAFE_EVIDENCE_ID_PATTERN.fullmatch(source.evidence_id)
            else stable_web_evidence_id(source.url, fallback=f"ev-ref-{source.citation_index}")
        )
        lines.append(f"[{source.citation_index}] evidence_id={evidence_id} status={source_status}")
    if len(lines) == 1 and unattempted_request_count <= 0:
        lines.append(render_runtime_prompt("research.workset_empty"))
    if unattempted_request_count > 0:
        # 只给数量，不回显 URL：用户原文属外部输入，不提升进 system；
        # 数量已足以让「两个都读到了」这类陈述与上下文直接冲突。
        lines.append(render_runtime_prompt("research.workset_unattempted", count=unattempted_request_count))
    return "\n".join(lines)


def count_unattempted_requested_urls(
    workset: ResearchEvidenceWorkset,
    requested_urls: Iterable[str],
) -> int:
    """本轮用户请求里既未读成功、也未发起过读取的 URL 数量。

    #127 的三个确证样本共同点是某个目标从未发起 url_read，因此它不会进入 workset，
    模型看到的状态清单是一份不完整的事实。这里补上缺口的规模。
    """

    touched = workset.successful_read_urls | workset.attempted_read_urls
    unattempted = {
        canonicalize_evidence_url(url)
        for url in requested_urls
        if url and canonicalize_evidence_url(url) not in touched
    }
    return len(unattempted)


def build_research_untrusted_context_messages(workset: ResearchEvidenceWorkset) -> list[PromptMessage]:
    """把外部派生摘要放回明确不可信的 user web_context，绝不提升为 system。"""

    messages: list[PromptMessage] = []
    for source in sorted(
        workset.sources.values(),
        key=lambda item: (item.citation_index, item.evidence_id),
    ):
        is_read = source.url_key in workset.successful_read_urls
        is_failed = source.url_key in workset.attempted_read_urls and not is_read
        if is_failed:
            continue
        facts = []
        if source.summary:
            facts.append(render_runtime_prompt("research.bounded_summary", summary=source.summary))
        if source.key_findings:
            facts.append(render_runtime_prompt("research.key_findings", findings="; ".join(source.key_findings)))
        if not facts:
            facts.append(render_runtime_prompt("research.identity_only"))
        content = format_untrusted_source_context(
            UntrustedSourceContext(
                source_id=source.evidence_id,
                source_type="url_read" if source.kind == "url_read" else "search",
                title=source.title,
                url=source.url,
                content=render_runtime_prompt("research.citation_number", citation_index=source.citation_index)
                + "\n"
                + "\n".join(facts),
                provider="web",
            ),
            max_chars=MAX_RESEARCH_SOURCE_CONTEXT_CHARS,
        )
        messages.append(PromptMessage(role="user", content=content))
    return messages


def _research_source_from_ref(
    ref: Any,
    summaries: dict[str, tuple[str, list[str]]],
) -> ResearchSource | None:
    raw_url = str(_value(ref, "url") or "").strip()
    canonical_url = canonicalize_evidence_url(raw_url)
    if not canonical_url or len(raw_url) > MAX_RESEARCH_SOURCE_URL_CHARS:
        return None
    citation_index = _value(ref, "citation_index")
    if not isinstance(citation_index, int) or isinstance(citation_index, bool) or citation_index < 1:
        return None
    evidence_id = str(
        _value(ref, "evidence_id") or stable_web_evidence_id(canonical_url, fallback=f"ev-ref-{citation_index}")
    )
    summary, key_findings = summaries.get(
        evidence_id, summaries.get(stable_web_evidence_id(raw_url, fallback=evidence_id), ("", []))
    )
    return ResearchSource(
        evidence_id=evidence_id,
        citation_index=citation_index,
        title=str(_value(ref, "title") or "网页来源")[:80],
        url=raw_url,
        kind=str(_value(ref, "kind") or "search"),
        summary=str(summary or "")[:180],
        key_findings=tuple(str(item)[:80] for item in key_findings[:5]),
    )


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
