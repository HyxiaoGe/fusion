"""候选整理仅保序去重，不干预模型阅读数量与来源选择。"""

import pytest

from app.schemas.chat import SearchSource
from app.services.search_read_planner import build_search_read_plan, format_search_read_plan_guidance
from app.services.source_candidate_ranker import SearchResultForRanking, rank_search_sources


def _result(sources, *, intent=None, query="项目年度报告", call_id="search-1"):
    return SearchResultForRanking(tool_call_id=call_id, query=query, sources=sources, intent=intent)


@pytest.mark.parametrize("intent", [None, "quick_fact", "freshness", "comparison", "deep_research", "official_source"])
def test_each_intent_preserves_all_provider_candidates_without_read_quota(intent):
    sources = [
        SearchSource(title="项目原始报告", url="https://local.example.cn/report", description="原始记录"),
        SearchSource(title="媒体转述", url="https://reuters.com/report", description="转述"),
        SearchSource(title="现场访谈", url="https://bilibili.com/video/1", description="访谈记录"),
        SearchSource(title="公开讨论", url="https://zhihu.com/question/1", description="讨论记录"),
    ]
    plan = build_search_read_plan([_result(sources, intent=intent)])
    assert [candidate.url for candidate in plan.candidates] == [source.url for source in sources]
    assert not plan.read_required
    assert plan.minimum_required_reads == 0
    assert not plan.recommended and not plan.low_priority
    assert all(decision.action == "keep_candidate" for decision in plan.read_decisions)
    guidance = format_search_read_plan_guidance(plan)
    assert "Read at most" not in guidance and "Read at least" not in guidance
    assert "priority" not in guidance.lower()
    assert all(source.url in guidance for source in sources)


def test_deduplication_keeps_first_raw_url_and_query_across_batches():
    first_url = "https://www.example.com/report/?utm_source=newsletter&b=2&a=1#intro"
    plan = rank_search_sources(
        [
            _result([{"title": "原报告", "url": first_url, "description": "正文"}]),
            _result(
                [{"title": "转载", "url": "https://example.com/report?a=1&b=2", "description": "重复"}],
                query="补充查证",
                call_id="search-2",
            ),
        ]
    )
    assert plan.total_source_count == 2 and plan.unique_source_count == 1
    assert plan.candidates[0].url == first_url
    assert plan.candidates[0].query == "项目年度报告"
    assert plan.candidates[0].tool_call_id == "search-1"


def test_legacy_read_controls_do_not_hide_any_candidates():
    sources = [SearchSource(title=str(i), url=f"https://example.org/{i}", description="正文") for i in range(5)]
    plan = rank_search_sources([_result(sources)], max_recommended=1, read_required=True, minimum_required_reads=1)
    assert len(plan.candidates) == 5
    assert not plan.read_required and plan.minimum_required_reads == 0
    assert len(plan.read_decisions) == 5


def test_same_title_at_different_urls_is_not_dropped():
    plan = rank_search_sources(
        [
            _result(
                [
                    {"title": "年度报告", "url": "https://example.org/2025"},
                    {"title": "年度报告", "url": "https://example.org/2026"},
                ]
            )
        ]
    )
    assert len(plan.candidates) == 2


def test_malformed_source_url_does_not_discard_other_candidates():
    plan = rank_search_sources(
        [
            _result(
                [
                    {"title": "错误端口", "url": "https://example.org:invalid/report"},
                    {"title": "有效来源", "url": "https://example.org/report"},
                ]
            )
        ]
    )
    assert [candidate.title for candidate in plan.candidates] == ["错误端口", "有效来源"]


def test_empty_plan_has_no_guidance():
    plan = build_search_read_plan([])
    assert plan.unique_source_count == 0
    assert format_search_read_plan_guidance(plan) == ""
