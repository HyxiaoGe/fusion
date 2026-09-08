"""模型搜索数量与互补查询必须穿透旧运行配置。"""

import unittest
from unittest.mock import patch

from app.services.stream.network_budget import NetworkToolBudget


class ModelSearchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "search": {
                "standard_budget": {"name": "standard", "requested_count": 5, "context_source_limit": 5},
                "budgets_by_intent": {
                    "quick_fact": {"name": "quick_fact", "requested_count": 3, "context_source_limit": 3}
                },
                "followup_budgets_by_name": {
                    "quick_fact": {"name": "quick_fact_followup", "requested_count": 3, "context_source_limit": 3}
                },
                "thresholds": {"similar_followup": 0.55, "duplicate_search": 0.82},
            },
            "network": {
                "max_search_calls": 40,
                "default_planned_search_calls": 2,
                "repair_search_count": 3,
                "repair_context_source_limit": 3,
            },
        }
        self.enterContext(
            patch("app.services.stream.network_budget.get_agent_strategy_config", return_value=(self.config, None))
        )

    def test_legacy_config_cannot_override_requested_count(self):
        for supplied, expected in ((None, 10), (1, 1), (10, 10), (16, 16), (99, 20)):
            with self.subTest(count=supplied):
                args, result = NetworkToolBudget().prepare_web_search_args(
                    {"query": "北京人口", "intent": "quick_fact", "count": supplied}
                )
                self.assertIsNone(result)
                self.assertEqual(args["count"], expected)
                self.assertEqual(args["context_source_limit"], min(expected, 10))

    def test_complementary_search_passes_old_similarity_and_plan_limits(self):
        budget = NetworkToolBudget()
        for query in (
            "香港未来三天天气",
            "香港未来三天天气 天文台",
            "香港未来三天天气 降雨概率",
            "香港未来三天天气 气温",
        ):
            with self.subTest(query=query):
                args, result = budget.prepare_web_search_args({"query": query, "count": 12})
                self.assertIsNone(result)
                self.assertEqual(args["count"], 12)
        self.assertEqual(budget.web_search_calls, 4)

    def test_unread_candidates_do_not_prevent_complementary_search(self):
        budget = NetworkToolBudget()
        _args, result = budget.prepare_web_search_args({"query": "香港天文台降雨概率"})
        self.assertIsNone(result)
        self.assertEqual(budget.web_search_calls, 1)

    def test_search_signature_includes_provider_filters_and_count(self):
        budget = NetworkToolBudget()
        for overrides in ({}, {"domains": ["hko.gov.hk"]}, {"recency_days": 1}, {"count": 20}):
            with self.subTest(overrides=overrides):
                _args, result = budget.prepare_web_search_args({"query": "香港天气", **overrides})
                self.assertIsNone(result)
        _args, duplicate = budget.prepare_web_search_args({"query": " 香港天气 ", "count": 20})
        self.assertIsNone(duplicate)
        self.assertEqual(budget.web_search_calls, 5)

    def test_search_preserves_explicit_count(self):
        budget = NetworkToolBudget()
        args, result = budget.prepare_web_search_args({"query": "香港天气", "count": 15})
        self.assertIsNone(result)
        self.assertEqual(args["count"], 15)
        self.assertEqual(args["context_source_limit"], 10)

    def test_invalid_count_uses_default_or_safe_range(self):
        for count, expected in ((False, 10), ("invalid", 10), (-2, 1), (0, 1), (float("inf"), 10)):
            with self.subTest(count=count):
                args, result = NetworkToolBudget().prepare_web_search_args({"query": "信息", "count": count})
                self.assertIsNone(result)
                self.assertEqual(args["count"], expected)

    def test_reordered_domains_and_identical_provider_request_both_execute(self):
        budget = NetworkToolBudget()
        first, first_result = budget.prepare_web_search_args(
            {"query": "香港天气", "domains": ["hko.gov.hk", "weather.gov.hk"], "intent": "quick_fact"}
        )
        _args, duplicate = budget.prepare_web_search_args(
            {"query": "香港天气", "domains": ["weather.gov.hk", "hko.gov.hk"], "intent": "unsupported"}
        )
        self.assertIsNone(first_result)
        self.assertIsNone(duplicate)
        self.assertEqual(budget.web_search_calls, 2)

    def test_search_does_not_bypass_global_call_budget(self):
        budget = NetworkToolBudget(web_search_calls=40)
        _args, result = budget.prepare_web_search_args({"query": "香港天气", "count": 20})
        self.assertTrue(result.data["budget_limited"])
        self.assertEqual(budget.web_search_calls, 40)
