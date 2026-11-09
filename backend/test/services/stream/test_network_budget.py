import unittest
from copy import deepcopy
from unittest.mock import patch

from app.services.config_defaults import DEFAULT_AGENT_STRATEGY_CONFIG
from app.services.stream import network_budget as network_budget_module
from app.services.stream.network_budget import NetworkToolBudget


def _low_network_budget():
    """用显式低预算保留触顶边界验证。"""
    config = deepcopy(DEFAULT_AGENT_STRATEGY_CONFIG)
    config["network"].update(max_search_calls=4, max_url_read_calls=5)
    return patch.object(network_budget_module, "get_agent_strategy_config", return_value=config)


class NetworkToolBudgetTests(unittest.TestCase):
    def test_default_search_budget_allows_forty_provider_calls(self):
        for config in (deepcopy(DEFAULT_AGENT_STRATEGY_CONFIG), {}):
            with (
                self.subTest(configured=bool(config)),
                patch.object(network_budget_module, "get_agent_strategy_config", return_value=config),
            ):
                budget = NetworkToolBudget()
                for index in range(40):
                    _args, degraded = budget.prepare_web_search_args({"query": f"核对资料 {index}"})
                    self.assertIsNone(degraded, f"第 {index + 1} 次搜索不应被预算阻断")
                self.assertEqual(budget.web_search_calls, 40)
                args, degraded = budget.prepare_web_search_args({"query": "核对最后一份资料"})
                self.assertTrue(degraded.data["budget_limited"])
                self.assertEqual(args["budget_decision"]["reason_code"], "hard_search_limit_reached")
                self.assertEqual(budget.web_search_calls, 40)

    def test_default_read_budget_allows_one_hundred_provider_calls(self):
        for config in (deepcopy(DEFAULT_AGENT_STRATEGY_CONFIG), {}):
            with (
                self.subTest(configured=bool(config)),
                patch.object(network_budget_module, "get_agent_strategy_config", return_value=config),
            ):
                budget = NetworkToolBudget()
                for index in range(100):
                    _args, degraded = budget.prepare_url_read_args({"url": f"https://example.com/{index}"})
                    self.assertIsNone(degraded, f"第 {index + 1} 次读取不应被预算阻断")
                self.assertEqual(budget.url_read_calls, 100)
                _args, degraded = budget.prepare_url_read_args({"url": "https://example.com/last"})
                self.assertTrue(degraded.data["budget_limited"])
                self.assertEqual(budget.url_read_calls, 100)

    def test_model_arguments_reach_the_search_service_unchanged(self):
        """条数、时效、限定网站由搜索服务检查并告诉模型哪里不对，这里不清洗、不截断、不丢弃。"""
        budget = NetworkToolBudget()
        model_args = {
            "query": "redis",
            "count": 99,
            "recency_days": 0,
            "domains": ["https://Redis.io/docs", "bad domain"],
        }

        args, degraded = budget.prepare_web_search_args(dict(model_args))

        self.assertIsNone(degraded)
        self.assertEqual({key: args[key] for key in model_args}, model_args)
        self.assertEqual(args["search_budget"], "standard")

    def test_context_source_limit_follows_model_count_up_to_ten(self):
        for count, expected in ((3, 3), (None, 10), (15, 10), (99, 10), ("bad", 10)):
            with self.subTest(count=count):
                args, _degraded = NetworkToolBudget().prepare_web_search_args({"query": "redis", "count": count})
                self.assertEqual(args["context_source_limit"], expected)

    def test_initial_search_records_budget_decision(self):
        budget = NetworkToolBudget()

        args, degraded = budget.prepare_web_search_args({"query": "OpenAI 最新公告 2026年7月", "count": 6})

        self.assertIsNone(degraded)
        self.assertEqual(
            args["budget_decision"],
            {
                "query": "OpenAI 最新公告 2026年7月",
                "action": "execute",
                "budget_name": "standard",
                "requested_count": 6,
                "context_source_limit": 6,
                "reason_code": "initial_search",
                "previous_query_count": 0,
                "planned_search_limit": 40,
            },
        )

    def test_followups_and_repeated_queries_are_complementary_searches(self):
        self.enterContext(_low_network_budget())
        budget = NetworkToolBudget()

        budget.prepare_web_search_args({"query": "OpenAI 2026 最新产品"})
        second_args, second_degraded = budget.prepare_web_search_args({"query": "OpenAI 2026 最新产品"})
        third_args, third_degraded = budget.prepare_web_search_args({"query": "OpenAI 权威媒体 2026 最新"})

        self.assertIsNone(second_degraded)
        self.assertIsNone(third_degraded)
        self.assertEqual(second_args["budget_decision"]["reason_code"], "complementary_search")
        self.assertEqual(third_args["budget_decision"]["previous_query_count"], 2)
        self.assertEqual(budget.web_search_calls, 3)
        self.assertEqual(len(budget.web_search_queries), 3)

    def test_web_search_hard_cap_returns_degraded_without_consuming_handler(self):
        self.enterContext(_low_network_budget())
        budget = NetworkToolBudget(web_search_calls=4)

        args, degraded = budget.prepare_web_search_args({"query": "q4", "count": 8, "domains": ["redis.io"]})

        self.assertEqual(args["query"], "q4")
        self.assertEqual(degraded.status, "degraded")
        self.assertTrue(degraded.data["budget_limited"])
        self.assertEqual(degraded.data["requested_count"], 8)
        self.assertEqual(degraded.data["context_source_limit"], 8)
        self.assertEqual(degraded.data["domains"], ["redis.io"])
        self.assertEqual(args["budget_decision"]["action"], "limit_budget")
        self.assertEqual(budget.web_search_calls, 4)

    def test_standard_sixth_url_read_returns_degraded(self):
        self.enterContext(_low_network_budget())
        budget = NetworkToolBudget()

        for i in range(5):
            _args, degraded = budget.prepare_url_read_args({"url": f"https://example.com/{i}"})
            self.assertIsNone(degraded)

        args, degraded = budget.prepare_url_read_args({"url": "https://example.com/5"})

        self.assertEqual(args["url"], "https://example.com/5")
        self.assertIsNotNone(degraded)
        self.assertEqual(degraded.status, "degraded")
        self.assertTrue(degraded.data["budget_limited"])


if __name__ == "__main__":
    unittest.main()
