import unittest

from scripts.backfill_model_context_windows import plan_updates


def _entry(name, underlying, model_id, *, db_model=True, max_input_tokens=None):
    info = {"id": model_id, "db_model": db_model}
    if max_input_tokens is not None:
        info["max_input_tokens"] = max_input_tokens
    return {"model_name": name, "litellm_params": {"model": underlying}, "model_info": info}


class PlanUpdatesTests(unittest.TestCase):
    def test_fills_only_missing_windows_with_known_sources(self):
        planned = plan_updates(
            [
                _entry("mimo-v2.6-pro", "openai/mimo-v2.6-pro", "a"),
                _entry("kimi-k3", "moonshot/kimi-k3", "b", max_input_tokens=1048576),
                _entry("qwen-vl-max", "openai/qwen-vl-max", "c"),
                _entry("config-route", "openai/mimo-v2.6-pro", "d", db_model=False),
            ]
        )

        self.assertEqual([(p["model_id"], p["max_input_tokens"]) for p in planned], [("a", 1048576)])

    def test_invalid_existing_window_is_treated_as_missing(self):
        planned = plan_updates(
            [
                _entry("qwen3.8-max", "openai/qwen3.8-max", "a", max_input_tokens=0),
                _entry("qwen3.8-max", "openai/qwen3.8-max", "a"),
            ]
        )

        self.assertEqual([(p["model_id"], p["max_input_tokens"]) for p in planned], [("a", 991808)])


if __name__ == "__main__":
    unittest.main()
