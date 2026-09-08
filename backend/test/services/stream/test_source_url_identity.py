"""原始 URL 与统一来源身份贯穿搜索、引用、研究门禁及恢复。"""

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.chat import SearchBlock, SearchSource, SearchSourceSummary, SourceReference, UrlBlock
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.network_budget import NetworkToolBudget
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    assign_missing_source_reference_metadata,
    build_research_untrusted_context_messages,
    validate_research_completion,
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


@pytest.mark.parametrize(
    "urls",
    [
        ("https://example.org/report?utm_id=one", "https://example.org/report?utm_id=two"),
        ("https://www.example.org/report/", "https://example.org/report"),
        ("https://example.org:443/report/?utm_campaign=one#chapter", "https://example.org/report?utm_custom=two"),
    ],
)
def test_tracking_variants_do_not_satisfy_two_distinct_research_sources(urls):
    blocks = []
    workset = ResearchEvidenceWorkset()
    budget = NetworkToolBudget(require_distinct_read_urls=True)
    indexes = []
    rejected = []
    for index, url in enumerate(urls, 1):
        record = _search_record(url, index)
        search_block = _block(record, blocks)
        blocks.append(search_block)
        workset.record_content_blocks([search_block])
        indexes.append(search_block.source_refs[0].citation_index)
        args, rejection = budget.prepare_url_read_args({"url": url}, plan_item_id=f"read-{index}")
        rejected.append(rejection is not None)
        if rejection is not None:
            continue
        read = ToolExecutionRecord(
            tool_call={"id": f"read-{index}", "name": "url_read", "arguments": args},
            result=ToolResult(status="success", data={"url": url, "title": "报告", "content": "同一正文"}),
            handler=UrlReadHandler(),
            block_id=f"read-block-{index}",
            log_id=f"read-log-{index}",
        )
        read_block = _block(read, blocks)
        blocks.append(read_block)
        workset.record_content_blocks([read_block])
    observed = {
        "citations": indexes,
        "read_rejected": rejected,
        "read_count": len(workset.successful_read_urls),
        "research_complete": validate_research_completion(workset, "结论[1][2]").is_valid,
    }
    assert observed == {
        "citations": [1, 1],
        "read_rejected": [False, True],
        "read_count": 1,
        "research_complete": False,
    }


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


def test_business_query_parameters_keep_sources_distinct():
    from app.services.source_evidence_ledger import canonicalize_evidence_url

    first = "https://example.org/report?id=one&utm_id=x"
    second = "https://example.org/report?id=two&utm_id=y"
    assert canonicalize_evidence_url(first) == "https://example.org/report?id=one"
    assert canonicalize_evidence_url(second) == "https://example.org/report?id=two"
    budget = NetworkToolBudget(require_distinct_read_urls=True)
    assert budget.prepare_url_read_args({"url": first}, plan_item_id="one")[1] is None
    assert budget.prepare_url_read_args({"url": second}, plan_item_id="two")[1] is None


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
