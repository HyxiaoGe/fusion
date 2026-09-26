import unittest


class AgentStrategyConfigTests(unittest.TestCase):
    def test_default_agent_strategy_config_contains_required_sections(self):
        from app.services.agent_strategy_config import DEFAULT_AGENT_STRATEGY_CONFIG, get_agent_strategy_config

        config, meta = get_agent_strategy_config()

        self.assertEqual(meta["namespace"], "agent_strategy")
        self.assertIn("model_runtime", DEFAULT_AGENT_STRATEGY_CONFIG)
        self.assertIn("search", config)
        self.assertIn("network", config)
        self.assertIn("tool_context", config)

    def test_get_agent_strategy_config_allows_test_override(self):
        from app.services.agent_strategy_config import get_agent_strategy_config

        config, meta = get_agent_strategy_config(
            override={
                "network": {"max_search_calls": 12},
            }
        )

        self.assertEqual(config["network"]["max_search_calls"], 12)
        self.assertIn("max_url_read_calls", config["network"])
        self.assertEqual(meta["source"], "override")


if __name__ == "__main__":
    unittest.main()
