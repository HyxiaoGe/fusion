"""原始 URL 与统一来源身份贯穿搜索、引用、研究门禁及恢复。"""

import asyncio
from types import SimpleNamespace

from app.schemas.chat import SearchBlock, SearchSource, SearchSourceSummary, SourceReference, UrlBlock
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    assign_missing_source_reference_metadata,
    build_research_untrusted_context_messages,
)
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import (
    _emit_citation_source_evidence,
    append_tool_round_messages,
)
from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import UrlReadHandler
from app.services.tool_handlers.web_search import WebSearchHandler, _post_process_sources

RAW_URL = "https://www.example.org/report?utm_source=feed#chapter"


def _search_record(url, index):
    sources = _post_process_sources(
        [SearchSource(title="同一原始报告", url=url, description="实际报告正文。")],
        intent=None,
        domains=[],
    )
    return ToolExecutionRecord(
        tool_call={"id": f"search-{index}", "name": "web_search", "arguments": {"query": f"查询{index}"}},
        result=ToolResult(status="success", data={"query": f"查询{index}", "sources": sources}),
        handler=WebSearchHandler(),
        block_id=f"search-block-{index}",
        log_id=f"search-log-{index}",
    )


def _block(record, previous):
    request = SimpleNamespace(
        content_blocks=list(previous),
        messages=[],
        tool_calls=[record.tool_call],
        reasoning_buf="",
        should_use_reasoning=False,
    )
    append_tool_round_messages(request, [record])
    return request.content_blocks[-1]


def test_citation_and_used_events_preserve_original_url():
    record = _search_record(RAW_URL, 1)
    block = _block(record, [])
    emitted = []

    async def emit(**kwargs):
        emitted.append(kwargs["evidence"])

    asyncio.run(
        _emit_citation_source_evidence(
            SimpleNamespace(emitter=SimpleNamespace(evidence_item_upserted=emit)),
            results=[record],
            built_content_blocks={"search-1": block},
        )
    )
    used = build_used_final_answer_evidence(content_blocks=[block], answer_text="结论[1]")
    assert (emitted[0]["url"], used[0]["url"]) == (RAW_URL, RAW_URL)


def test_research_restored_context_preserves_original_url():
    record = _search_record(RAW_URL, 1)
    workset = ResearchEvidenceWorkset()
    workset.record_content_blocks([_block(record, [])])
    restored = build_research_untrusted_context_messages(workset)[0].content
    assert RAW_URL in restored


def _legacy_block(url, *, index, evidence_id, block_id="legacy"):
    return SearchBlock(
        type="search",
        id=block_id,
        query="旧查询",
        sources=[SearchSourceSummary(title="旧报告", url=url)],
        source_refs=[
            SourceReference(kind="search", title="旧报告", url=url, citation_index=index, evidence_id=evidence_id)
        ],
    )


def test_new_raw_alias_inherits_legacy_citation_and_evidence_identity():
    old = _legacy_block("https://example.org/report/", index=7, evidence_id="ev-legacy-report")
    raw = "https://www.example.org/report/?utm_id=new#chapter"
    block = _block(_search_record(raw, 1), [old])
    assert block.source_refs[0].citation_index == 7
    assert block.source_refs[0].evidence_id == "ev-legacy-report"
    assert block.source_refs[0].url == raw


def test_historical_alias_indexes_remain_reserved_and_explicit_answers_keep_aliases():
    first = _legacy_block("https://example.org/report", index=7, evidence_id="ev-first", block_id="old-first")
    alias_url = "https://example.org/report/?utm_id=historical"
    second = _legacy_block(alias_url, index=19, evidence_id="ev-second", block_id="old-second")
    new = _block(_search_record("https://other.example.org/report", 3), [first, second])
    assert new.source_refs[0].citation_index == 20
    assert first.source_refs[0].citation_index == 7 and second.source_refs[0].citation_index == 19
    used = build_used_final_answer_evidence(
        content_blocks=[first, second],
        answer_text="来源结论[19]",
        evidence_policy="deep_research_v1",
        allowed_citation_indexes={7, 19},
    )
    assert [(item["id"], item["url"], item["citation_index"]) for item in used] == [("ev-second", alias_url, 19)]


def test_legacy_metadata_backfill_keeps_raw_urls_and_inherits_explicit_identity():
    old = _legacy_block("https://example.org/report/", index=7, evidence_id="ev-legacy-report")
    raw = "https://www.example.org/report?utm_id=new#chapter"
    new = UrlBlock(type="url_read", url=raw, title="阅读结果")
    assign_missing_source_reference_metadata([old, new])
    assert new.source_refs[0]["url"] == raw
    assert new.source_refs[0]["citation_index"] == 7
    assert new.source_refs[0]["evidence_id"] == "ev-legacy-report"


def test_research_read_uses_full_identity_and_restores_long_original_url():
    raw = "https://www.example.org/report?document=" + "a" * 600 + "&utm_id=one#chapter"
    record = ToolExecutionRecord(
        tool_call={"id": "read-long", "name": "url_read", "arguments": {"url": raw}},
        result=ToolResult(status="success", data={"url": raw, "title": "长链接", "content": "原文"}),
        handler=UrlReadHandler(),
        block_id="read-long",
        log_id="log-long",
    )
    workset = ResearchEvidenceWorkset()
    workset.record_content_blocks([_block(record, [])])
    assert len(workset.successful_read_urls) == 1
    assert workset.valid_citation_indexes == {1}
    context = build_research_untrusted_context_messages(workset, include_candidates=False)[0].content
    assert raw.replace("&", "&amp;") in context
