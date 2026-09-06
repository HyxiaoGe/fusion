import unittest
from unittest.mock import patch

from app.schemas.chat import SearchSource
from app.services import source_candidate_ranker as source_candidate_ranker_module
from app.services.source_candidate_ranker import (
    SearchResultForRanking,
    format_source_selection_guidance,
    rank_search_sources,
)


class SourceCandidateRankerTests(unittest.TestCase):
    def _rankable_sources(self):
        return [
            SearchSource(
                title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                url="https://openai.com/index/previewing-gpt-5-6-sol",
                description="OpenAI official announcement.",
            ),
            SearchSource(
                title="[PDF] GPT-5.6 Preview System Card - Deployment Safety Hub",
                url="https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf",
                description="Official system card PDF.",
            ),
            SearchSource(
                title="OpenAI releases powerful new GPT-5.6 model - Axios",
                url="https://axios.com/2026/06/26/openai-gpt-sol-terra-luna-trump",
                description="Axios reports on OpenAI GPT-5.6 release restrictions.",
            ),
            SearchSource(
                title="GPT-5.6 解读视频",
                url="https://youtube.com/watch?v=abc",
                description="A video commentary.",
            ),
        ]

    def test_rank_search_sources_deduplicates_and_recommends_high_value_sources(self):
        search_results = [
            SearchResultForRanking(
                tool_call_id="search-1",
                query="OpenAI GPT-5.6 Sol 发布 2026年6月 新闻",
                sources=[
                    SearchSource(
                        title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                        url="https://openai.com/index/previewing-gpt-5-6-sol?utm_source=feed",
                        description="OpenAI official announcement for GPT-5.6 Sol.",
                    ),
                    SearchSource(
                        title="OpenAI 新闻中心| 最新动态",
                        url="https://openai.com/zh-Hans-CN/news/company-announcements",
                        description="OpenAI company announcements.",
                    ),
                    SearchSource(
                        title="不過，這次GPT-5.6 並未全面開放。應美國政府要求，OpenAI 先以 ...",
                        url="https://threads.com/@kufutw/post/DaJ2_DLD8cS",
                        description="Social repost about GPT-5.6.",
                    ),
                    SearchSource(
                        title="GPT-5.6 Sol 解读视频",
                        url="https://youtube.com/watch?v=abc",
                        description="A video commentary.",
                    ),
                    SearchSource(
                        title="OpenAI 的閃電戰：GPT-5.6 亮相與全棧帝國的終局構想 - Yahoo 財經",
                        url="https://hk.finance.yahoo.com/news/openai-gpt-5-6.html",
                        description="Syndicated finance article.",
                    ),
                ],
            ),
            SearchResultForRanking(
                tool_call_id="search-2",
                query="OpenAI GPT-5.6 Sol 2026年6月 官方公告",
                sources=[
                    SearchSource(
                        title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                        url="https://openai.com/index/previewing-gpt-5-6-sol",
                        description="Duplicate official announcement.",
                    ),
                    SearchSource(
                        title="OpenAI releases powerful new GPT-5.6 model - Axios",
                        url="https://axios.com/2026/06/26/openai-gpt-sol-terra-luna-trump",
                        description="Axios reports on OpenAI GPT-5.6 release restrictions.",
                    ),
                    SearchSource(
                        title="[PDF] GPT-5.6 Preview System Card - Deployment Safety Hub",
                        url="https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf",
                        description="Official system card PDF.",
                    ),
                    SearchSource(
                        title="ChatGPT 5.6 Sol: Release Date, Price & Review | Coursiv Blog",
                        url="https://coursiv.io/blog/chatgpt-5-6-sol",
                        description="SEO blog summary.",
                    ),
                    SearchSource(
                        title="OpenAI limits new AI models to trusted partners request US government",
                        url="https://cnbc.com/2026/06/26/openai-limits-new-ai-models-to-trusted-partners-request-us-government.html",
                        description="Media report about OpenAI restrictions.",
                    ),
                ],
            ),
        ]

        plan = rank_search_sources(search_results, max_recommended=3)

        self.assertEqual(plan.total_source_count, 10)
        self.assertEqual(plan.unique_source_count, 9)
        self.assertEqual(
            [candidate.url for candidate in plan.recommended],
            [
                "https://openai.com/index/previewing-gpt-5-6-sol",
                "https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf",
                "https://axios.com/2026/06/26/openai-gpt-sol-terra-luna-trump",
            ],
        )
        self.assertIn("official source", plan.recommended[0].reasons)
        self.assertIn("primary announcement", plan.recommended[0].reasons)
        self.assertIn("official PDF or technical report", plan.recommended[1].reasons)
        self.assertIn("authoritative media", plan.recommended[2].reasons)

        by_domain = {candidate.domain: candidate for candidate in plan.candidates}
        self.assertEqual(by_domain["threads.com"].priority, "low")
        self.assertIn("social or forum source deprioritized by default", by_domain["threads.com"].reasons)
        self.assertEqual(by_domain["youtube.com"].priority, "low")
        self.assertIn("video source deprioritized by default", by_domain["youtube.com"].reasons)

    def test_rank_search_sources_uses_configured_authority_media_domain(self):
        with patch.object(
            source_candidate_ranker_module,
            "get_agent_strategy_config",
            return_value=(
                {
                    "source_ranker": {
                        "authority_media_domains": ["example.com"],
                        "low_priority_domains": [],
                        "video_domains": [],
                        "forum_domains": [],
                        "max_low_priority_examples": 3,
                        "weights": {
                            "source_order_base": 22,
                            "source_order_step": 2,
                            "official": 38,
                            "original": 22,
                            "specific_original": 18,
                            "official_original": 35,
                            "pdf": 35,
                            "authority_media": 36,
                            "listing_penalty": 28,
                            "video_penalty": 28,
                            "forum_penalty": 24,
                            "low_priority_penalty": 18,
                            "relevance_per_term": 4,
                            "relevance_max": 18,
                        },
                        "priority_thresholds": {
                            "high": 60,
                            "medium": 30,
                        },
                    }
                },
                {"source": "test"},
            ),
            create=True,
        ):
            plan = rank_search_sources(
                [
                    SearchResultForRanking(
                        tool_call_id="search-config",
                        query="example product announcement",
                        sources=[
                            SearchSource(
                                title="Example reports product announcement",
                                url="https://example.com/news/product-announcement",
                                description="Example media report.",
                            )
                        ],
                    )
                ],
                max_recommended=1,
            )

        self.assertEqual(plan.recommended[0].domain, "example.com")
        self.assertIn("authoritative media", plan.recommended[0].reasons)

    def test_format_source_selection_guidance_explains_recommended_reads(self):
        search_results = [
            SearchResultForRanking(
                tool_call_id="search-1",
                query="OpenAI GPT-5.6 Sol 发布 2026年6月 新闻",
                sources=[
                    SearchSource(
                        title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                        url="https://openai.com/index/previewing-gpt-5-6-sol",
                        description="OpenAI official announcement.",
                    ),
                    SearchSource(
                        title="不過，這次GPT-5.6 並未全面開放。應美國政府要求，OpenAI 先以 ...",
                        url="https://threads.com/@kufutw/post/DaJ2_DLD8cS",
                        description="Social repost.",
                    ),
                ],
            )
        ]
        plan = rank_search_sources(search_results, max_recommended=1)

        guidance = format_source_selection_guidance(plan)

        self.assertIn("Structured source-selection guidance", guidance)
        self.assertIn("2 combined candidates and 2 after deduplication", guidance)
        self.assertIn("Recommended for priority reading", guidance)
        self.assertIn("Previewing GPT-5.6 Sol", guidance)
        self.assertIn("official source", guidance)
        self.assertIn("Low-priority candidates", guidance)
        self.assertIn("threads.com", guidance)
        self.assertIn("Do not read every search result merely for completeness", guidance)

    def test_source_selection_plan_records_read_decisions_for_all_candidates(self):
        search_results = [
            SearchResultForRanking(
                tool_call_id="search-1",
                query="OpenAI GPT-5.6 Sol 官方公告 2026年",
                sources=self._rankable_sources(),
            )
        ]

        plan = rank_search_sources(search_results, max_recommended=2)

        self.assertEqual(len(plan.read_decisions), plan.unique_source_count)
        self.assertEqual(
            [decision.action for decision in plan.read_decisions[:2]],
            ["recommend_read", "recommend_read"],
        )
        self.assertEqual(plan.decision_summary["recommend_read"], 2)
        self.assertEqual(plan.decision_summary["outside_read_limit"], 1)
        self.assertEqual(plan.decision_summary["low_priority_source_type"], 1)

    def test_low_priority_sources_are_deprioritized_with_reason_code(self):
        search_results = [
            SearchResultForRanking(
                tool_call_id="search-1",
                query="OpenAI GPT-5.6 Sol 官方公告 2026年",
                sources=self._rankable_sources(),
            )
        ]

        plan = rank_search_sources(search_results, max_recommended=2)

        low = next(decision for decision in plan.read_decisions if decision.candidate.domain == "youtube.com")
        self.assertEqual(low.action, "deprioritize")
        self.assertEqual(low.reason_code, "low_priority_source_type")

    def test_guidance_summarizes_not_recommended_reason_codes(self):
        search_results = [
            SearchResultForRanking(
                tool_call_id="search-1",
                query="OpenAI GPT-5.6 Sol 官方公告 2026年",
                sources=self._rankable_sources(),
            )
        ]
        plan = rank_search_sources(search_results, max_recommended=2)

        guidance = format_source_selection_guidance(plan)

        self.assertIn("Reasons not recommended", guidance)
        self.assertIn("low-priority source type", guidance)
        self.assertIn("outside the recommended reading limit", guidance)
        self.assertIn("only when recommended sources cannot answer an important fact", guidance)

    def test_search_read_planner_recommends_one_read_for_quick_fact(self):
        from app.services.search_read_planner import build_search_read_plan

        plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-quick",
                    query="OpenAI GPT-5.6 是什么 2026年",
                    sources=self._rankable_sources(),
                    intent="quick_fact",
                    search_budget="quick_fact",
                )
            ]
        )

        self.assertEqual(plan.recommended_read_limit, 1)
        self.assertEqual(len(plan.recommended), 1)
        self.assertTrue(plan.read_required)
        self.assertEqual(plan.minimum_required_reads, 1)
        self.assertEqual(plan.read_required_reason, "quick_fact_requires_verification")

    def test_search_read_planner_uses_configured_quick_fact_read_limit(self):
        from app.services import search_read_planner as search_read_planner_module

        with patch.object(
            search_read_planner_module,
            "get_agent_strategy_config",
            return_value=(
                {
                    "read_planner": {
                        "read_limits": {
                            "quick_fact": 2,
                            "standard": 2,
                            "deep": 3,
                        },
                        "quick_budget_names": ["quick_fact"],
                        "freshness_budget_names": ["freshness"],
                        "deep_budget_names": ["deep_research"],
                        "deep_intents": ["deep_research"],
                    }
                },
                {"source": "test"},
            ),
            create=True,
        ):
            plan = search_read_planner_module.build_search_read_plan(
                [
                    SearchResultForRanking(
                        tool_call_id="search-quick-config",
                        query="OpenAI GPT-5.6 是什么 2026年",
                        sources=self._rankable_sources(),
                        intent="quick_fact",
                        search_budget="quick_fact",
                    )
                ]
            )

        self.assertEqual(plan.recommended_read_limit, 2)
        self.assertEqual(len(plan.recommended), 2)

    def test_search_read_planner_recommends_two_reads_for_freshness(self):
        from app.services.search_read_planner import build_search_read_plan

        plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-fresh",
                    query="OpenAI GPT-5.6 最新公告 2026年6月",
                    sources=self._rankable_sources(),
                    intent="freshness",
                    search_budget="freshness",
                )
            ]
        )

        self.assertEqual(plan.recommended_read_limit, 2)
        self.assertEqual(len(plan.recommended), 2)
        self.assertTrue(plan.read_required)
        self.assertEqual(plan.minimum_required_reads, 1)
        self.assertEqual(plan.read_required_reason, "freshness_requires_verification")

    def test_search_read_planner_recommends_three_reads_for_official_or_comparison(self):
        from app.services.search_read_planner import build_search_read_plan

        official_plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-official",
                    query="OpenAI GPT-5.6 官方公告 2026年6月",
                    sources=self._rankable_sources(),
                    intent="official_source",
                    search_budget="official_source",
                )
            ]
        )
        comparison_plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-media",
                    query="OpenAI GPT-5.6 Reuters Axios 权威媒体报道",
                    sources=self._rankable_sources(),
                    intent="comparison",
                    search_budget="comparison",
                )
            ]
        )

        self.assertEqual(official_plan.recommended_read_limit, 3)
        self.assertEqual(len(official_plan.recommended), 3)
        self.assertEqual(comparison_plan.recommended_read_limit, 3)
        self.assertEqual(len(comparison_plan.recommended), 3)
        self.assertTrue(official_plan.read_required)
        self.assertEqual(official_plan.minimum_required_reads, 1)
        self.assertEqual(official_plan.read_required_reason, "official_source_requires_verification")
        self.assertTrue(comparison_plan.read_required)
        self.assertEqual(comparison_plan.minimum_required_reads, 1)
        self.assertEqual(comparison_plan.read_required_reason, "comparison_requires_verification")

    def test_search_read_planner_keeps_background_search_read_optional(self):
        from app.services.search_read_planner import build_search_read_plan, format_search_read_plan_guidance

        plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-background",
                    query="AI 编程助手 团队实践 案例",
                    sources=self._rankable_sources(),
                    intent=None,
                    search_budget="standard",
                )
            ]
        )

        guidance = format_search_read_plan_guidance(plan)

        self.assertEqual(plan.recommended_read_limit, 2)
        self.assertFalse(plan.read_required)
        self.assertEqual(plan.minimum_required_reads, 0)
        self.assertEqual(plan.read_required_reason, "")
        self.assertIn("if search snippets are insufficient", guidance)
        self.assertNotIn("Read at least", guidance)

    def test_search_read_plan_guidance_explains_read_limit_and_unrecommended_candidates(self):
        from app.services.search_read_planner import build_search_read_plan, format_search_read_plan_guidance

        plan = build_search_read_plan(
            [
                SearchResultForRanking(
                    tool_call_id="search-fresh",
                    query="OpenAI GPT-5.6 最新公告 2026年6月",
                    sources=self._rankable_sources(),
                    intent="freshness",
                    search_budget="freshness",
                )
            ]
        )

        guidance = format_search_read_plan_guidance(plan)

        self.assertIn("Search queries", guidance)
        self.assertIn("Read at most 2 recommended sources", guidance)
        self.assertIn("Read at least 1 recommended high-priority source", guidance)
        self.assertIn("Not recommended for deeper reading", guidance)
        self.assertIn("Do not read every search result merely for completeness", guidance)
