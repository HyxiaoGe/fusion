import unittest


class AgentStrategyConfigTests(unittest.TestCase):
    def test_agent_strategy_config_is_isolated_copy_of_code_defaults(self):
        from app.services.agent_strategy_config import get_agent_strategy_config
        from app.services.config_defaults import DEFAULT_AGENT_STRATEGY_CONFIG

        config = get_agent_strategy_config()
        self.assertEqual(config, DEFAULT_AGENT_STRATEGY_CONFIG)
        config["network"]["max_search_calls"] = 1
        self.assertEqual(get_agent_strategy_config()["network"]["max_search_calls"], 40)

    def test_agent_tools_disabled_aliases(self):
        from app.services.agent_strategy_config import get_agent_tools_disabled_aliases

        self.assertEqual(get_agent_tools_disabled_aliases(), {"qwen-vl-max"})


if __name__ == "__main__":
    unittest.main()
