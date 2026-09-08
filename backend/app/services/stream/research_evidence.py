"""深度研究的有界安全证据工作集与完成门禁。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.security.url_policy import MAX_URL_LENGTH
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.source_evidence_ledger import canonicalize_evidence_url, stable_web_evidence_id

MAX_RESEARCH_SOURCES = 12
MAX_RESEARCH_REPAIRS = 2
MAX_RESEARCH_SOURCE_CONTEXT_CHARS = 360
MAX_RESEARCH_SOURCE_URL_CHARS = MAX_URL_LENGTH
_CITATION_PATTERN = re.compile(r"(?:\[(\d{1,3})\]|⟦(\d{1,3})⟧)")
_SAFE_EVIDENCE_ID_PATTERN = re.compile(r"^ev-[A-Za-z0-9_-]{1,80}$")
DeepResearchStage = Literal["planning", "search", "read", "search_repair", "synthesis"]


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


@dataclass(frozen=True)
class ResearchCompletionResult:
    is_valid: bool
    reason: str = "valid"


@dataclass
class ResearchEvidenceWorkset:
    """仅保存本 run 新产生的安全来源投影，不接收原始网页或 MCP payload。"""

    sources: dict[str, ResearchSource] = field(default_factory=dict)
    successful_searches: int = 0
    successful_read_urls: set[str] = field(default_factory=set)
    attempted_read_urls: set[str] = field(default_factory=set)
    processed_block_ids: set[str] = field(default_factory=set)

    def record_content_blocks(
        self,
        content_blocks: list[Any],
        *,
        summaries: dict[str, tuple[str, list[str]]] | None = None,
        allow_read_success: bool = True,
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
            if allow_read_success and block_type == "url_read":
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
            if block_type == "search" and refs:
                self.successful_searches += 1

            for ref in refs:
                if (_value(ref, "status") or "success") != "success":
                    continue
                source = _research_source_from_ref(ref, safe_summaries)
                if source is None:
                    continue
                self.sources[source.evidence_id] = source
                if allow_read_success and (block_type == "url_read" or source.kind == "url_read"):
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

    @property
    def valid_citation_indexes(self) -> set[int]:
        return {
            source.citation_index for source in self.sources.values() if source.url_key in self.successful_read_urls
        }

    @property
    def unread_candidate_urls(self) -> set[str]:
        return {source.url for source in self.sources.values() if source.url_key not in self.attempted_read_urls}


def resolve_deep_research_stage(
    workset: ResearchEvidenceWorkset,
    *,
    has_valid_plan: bool,
    unexecuted_plan_tool_names: set[str] | None = None,
) -> DeepResearchStage:
    """只根据服务端状态决定下一轮工具阶段，不解析模型参数或外部正文。"""

    if not has_valid_plan:
        return "planning"
    if workset.successful_searches < 1:
        return "search"
    if len(workset.successful_read_urls) >= 2:
        remaining_tools = unexecuted_plan_tool_names or set()
        if remaining_tools - {"web_search", "url_read"}:
            return "planning"
        if "web_search" in remaining_tools:
            return "search"
        if "url_read" in remaining_tools:
            return "read" if workset.unread_candidate_urls else "search_repair"
        return "synthesis"
    if workset.unread_candidate_urls:
        return "read"
    return "search_repair"


def deep_research_stage_tool_names(stage: DeepResearchStage) -> frozenset[str] | None:
    """返回阶段工具白名单；None 表示保持原有工具集合。"""

    if stage == "search":
        return frozenset({"web_search"})
    if stage == "read":
        return frozenset({"url_read"})
    if stage == "search_repair":
        return frozenset({"web_search"})
    if stage == "synthesis":
        return frozenset()
    return None


def deep_research_stage_required_tool(stage: DeepResearchStage) -> str | None:
    allowed_tool_names = deep_research_stage_tool_names(stage)
    if allowed_tool_names is None or len(allowed_tool_names) != 1:
        return None
    return next(iter(allowed_tool_names))


def build_deep_research_stage_prompt(
    stage: DeepResearchStage,
    *,
    plan_repair_tool: str | None = None,
    active_plan_item_ids: list[str] | None = None,
) -> str:
    """生成不含任何外部来源内容的确定性阶段控制语。"""

    if plan_repair_tool:
        return render_runtime_prompt("research.stage_plan_repair", tool=plan_repair_tool)
    binding_prompt = ""
    if active_plan_item_ids:
        allowed_ids = ", ".join(f"`{item_id}`" for item_id in active_plan_item_ids)
        binding_prompt = render_runtime_prompt("research.stage_binding", allowed_ids=allowed_ids)
    if stage == "search":
        return render_runtime_prompt("research.stage_search", binding_prompt=binding_prompt)
    if stage == "read":
        return render_runtime_prompt("research.stage_read", binding_prompt=binding_prompt)
    if stage == "search_repair":
        return render_runtime_prompt("research.stage_search_repair", binding_prompt=binding_prompt)
    if stage == "synthesis":
        return render_runtime_prompt("research.stage_synthesis")
    return ""


def validate_research_completion(
    workset: ResearchEvidenceWorkset,
    answer_text: str,
) -> ResearchCompletionResult:
    if workset.successful_searches < 1:
        return ResearchCompletionResult(False, "missing_search")
    if len(workset.successful_read_urls) < 2:
        return ResearchCompletionResult(False, "insufficient_reads")

    citations = {
        int(match.group(1) or match.group(2))
        for match in _CITATION_PATTERN.finditer(answer_text or "")
        if match.group(1) or match.group(2)
    }
    if not citations:
        return ResearchCompletionResult(False, "missing_citation")
    if not citations.issubset(workset.valid_citation_indexes):
        return ResearchCompletionResult(False, "invalid_citation")
    return ResearchCompletionResult(True)


def build_research_workset_prompt(
    workset: ResearchEvidenceWorkset,
    *,
    include_candidates: bool = True,
) -> str:
    if not workset.sources:
        return ""
    lines = [render_runtime_prompt("research.workset_header")]
    for source in sorted(
        workset.sources.values(),
        key=lambda item: (item.citation_index, item.evidence_id),
    ):
        is_read = source.url_key in workset.successful_read_urls
        is_failed = source.url_key in workset.attempted_read_urls and not is_read
        if not is_read and not include_candidates:
            continue
        source_status = "read_success" if is_read else "read_failed" if is_failed else "candidate"
        evidence_id = (
            source.evidence_id
            if _SAFE_EVIDENCE_ID_PATTERN.fullmatch(source.evidence_id)
            else stable_web_evidence_id(source.url, fallback=f"ev-ref-{source.citation_index}")
        )
        retry_policy = " retry=forbidden" if is_failed else ""
        lines.append(f"[{source.citation_index}] evidence_id={evidence_id} status={source_status}{retry_policy}")
    if len(lines) == 1:
        lines.append(render_runtime_prompt("research.workset_empty"))
    return "\n".join(lines)


def build_research_untrusted_context_messages(
    workset: ResearchEvidenceWorkset,
    *,
    include_candidates: bool = True,
) -> list[PromptMessage]:
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
        if not is_read and not include_candidates:
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


def build_research_repair_prompt(reason: str, workset: ResearchEvidenceWorkset) -> str:
    if reason == "missing_search":
        action = render_runtime_prompt("research.repair_missing_search")
    elif reason == "insufficient_reads":
        action = render_runtime_prompt("research.repair_insufficient_reads")
    elif reason == "missing_citation":
        action = render_runtime_prompt("research.repair_missing_citation")
    else:
        action = render_runtime_prompt("research.repair_invalid_citation")
    workset_prompt = build_research_workset_prompt(workset)
    return render_runtime_prompt("research.repair", action=action, workset_prompt=workset_prompt)


def assign_missing_source_reference_metadata(content_blocks: list[Any]) -> None:
    """为旧 content block 补运行期稳定引用元数据；不引入原始正文。"""

    for block in content_blocks:
        if _value(block, "type") != "url_read" or _value(block, "source_refs"):
            continue
        url = str(_value(block, "url") or "")
        if not canonicalize_evidence_url(url):
            continue
        reference = {
            "kind": "url_read",
            "title": str(_value(block, "title") or "网页来源")[:80],
            "url": url,
            "favicon": _value(block, "favicon"),
            "status": _value(block, "status") or "success",
            "tool_call_log_id": str(_value(block, "tool_call_log_id") or ""),
            "error_message": _value(block, "error_message"),
        }
        _set_value(block, "source_refs", [reference])
        _set_value(block, "source_count", 1)

    registry: dict[str, int] = {}
    evidence_ids: dict[str, str] = {}
    max_index = 0
    for block in content_blocks:
        for ref in _value(block, "source_refs") or []:
            canonical_url = canonicalize_evidence_url(str(_value(ref, "url") or ""))
            citation_index = _value(ref, "citation_index")
            if isinstance(citation_index, int) and not isinstance(citation_index, bool) and citation_index > 0:
                max_index = max(max_index, citation_index)
            if (
                canonical_url
                and isinstance(citation_index, int)
                and not isinstance(citation_index, bool)
                and citation_index > 0
            ):
                registry.setdefault(canonical_url, citation_index)
            if canonical_url and _value(ref, "evidence_id"):
                evidence_ids.setdefault(canonical_url, str(_value(ref, "evidence_id")))

    for block in content_blocks:
        for ref in _value(block, "source_refs") or []:
            canonical_url = canonicalize_evidence_url(str(_value(ref, "url") or ""))
            if not canonical_url:
                continue
            citation_index = registry.get(canonical_url)
            if citation_index is None:
                max_index += 1
                citation_index = max_index
                registry[canonical_url] = citation_index
            evidence_id = evidence_ids.get(canonical_url) or stable_web_evidence_id(
                canonical_url,
                fallback=f"ev-ref-{citation_index}",
            )
            evidence_ids.setdefault(canonical_url, evidence_id)
            if isinstance(ref, dict):
                if not ref.get("evidence_id"):
                    ref["evidence_id"] = evidence_id
                if not ref.get("citation_index"):
                    ref["citation_index"] = citation_index
            else:
                if not _value(ref, "evidence_id"):
                    setattr(ref, "evidence_id", evidence_id)
                if not _value(ref, "citation_index"):
                    setattr(ref, "citation_index", citation_index)


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


def _set_value(value: Any, key: str, item: Any) -> None:
    if isinstance(value, dict):
        value[key] = item
    else:
        setattr(value, key, item)
