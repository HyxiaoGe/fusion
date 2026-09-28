import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api.models import _build_auto_card, _entry_to_card
from app.services.auto_model import pick_auto_model

CATALOG = {
    "mimo-v2.6-pro": {"db_model": True, "metadata": {"cost_tier": "mid", "capabilities": {"vision": True}}},
    "deepseek-chat": {"db_model": True, "metadata": {"cost_tier": "low", "capabilities": {}}},
    "qwen3.8-max": {"db_model": True, "metadata": {"cost_tier": "high", "capabilities": {"vision": True}}},
    "cheap-extra": {"db_model": True, "metadata": {"cost_tier": "low", "capabilities": {}}},
    "candidate/xiaomi/*": {"db_model": False, "metadata": {}},
}
CANDIDATES = ["mimo-v2.6-pro", "deepseek-chat", "qwen3.8-max"]


def _controls(**by_model):
    repo = MagicMock()
    repo.get_by_model_ids.side_effect = lambda ids: {key: value for key, value in by_model.items() if key in ids}
    return repo


class PickAutoModelTests(unittest.TestCase):
    def _pick(self, *, controls=None, unhealthy=(), catalog=CATALOG, require_vision=False):
        def capabilities(alias, **_kwargs):
            return catalog[alias]["metadata"].get("capabilities", {})

        with (
            patch("app.services.auto_model.litellm_catalog.list_aliases", return_value=dict(catalog)),
            patch("app.services.auto_model.litellm_catalog.get_capabilities", side_effect=capabilities),
            patch(
                "app.services.auto_model.litellm_health.get_health",
                side_effect=lambda alias: {"status": "unhealthy" if alias in unhealthy else "healthy"},
            ),
            patch("app.services.auto_model.get_auto_model_candidates", return_value=list(CANDIDATES)),
        ):
            return pick_auto_model(controls or _controls(), require_vision=require_vision)

    def test_picks_first_configured_candidate(self):
        self.assertEqual(self._pick(), "mimo-v2.6-pro")

    def test_skips_unhealthy_hidden_and_non_routable_candidates(self):
        controls = _controls(**{"deepseek-chat": SimpleNamespace(selectable=False, routable=True)})
        self.assertEqual(self._pick(controls=controls, unhealthy={"mimo-v2.6-pro"}), "qwen3.8-max")
        controls = _controls(**{"mimo-v2.6-pro": SimpleNamespace(selectable=True, routable=False)})
        self.assertEqual(self._pick(controls=controls), "deepseek-chat")

    def test_unregistered_candidate_is_skipped(self):
        catalog = {key: value for key, value in CATALOG.items() if key != "mimo-v2.6-pro"}
        self.assertEqual(self._pick(catalog=catalog), "deepseek-chat")

    def test_falls_back_to_rest_of_catalog_by_cost_tier_when_all_candidates_unusable(self):
        self.assertEqual(self._pick(unhealthy=set(CANDIDATES)), "cheap-extra")

    def test_returns_none_when_nothing_is_usable(self):
        self.assertIsNone(self._pick(unhealthy={*CANDIDATES, "cheap-extra"}))

    def test_require_vision_skips_text_only_models(self):
        self.assertEqual(self._pick(unhealthy={"mimo-v2.6-pro"}, require_vision=True), "qwen3.8-max")

    def test_require_vision_relaxes_when_no_vision_model_is_usable(self):
        self.assertEqual(
            self._pick(unhealthy={"mimo-v2.6-pro", "qwen3.8-max"}, require_vision=True),
            "deepseek-chat",
        )

    def test_empty_catalog_trusts_first_candidate(self):
        self.assertEqual(self._pick(catalog={}), "mimo-v2.6-pro")


class AutoModelCardTests(unittest.TestCase):
    def _cards(self):
        with patch("app.api.models.litellm_health.get_health", return_value={"status": "healthy"}):
            return [
                _entry_to_card(alias, {**entry, "max_input_tokens": 128000})
                for alias, entry in CATALOG.items()
                if entry["db_model"]
            ]

    def test_auto_card_unions_candidate_capabilities_and_reports_default_pick(self):
        cards = self._cards()
        with (
            patch("app.api.models.pick_auto_model", return_value="deepseek-chat"),
            patch("app.api.models.get_auto_model_candidates", return_value=["deepseek-chat", "qwen3.8-max"]),
        ):
            card = _build_auto_card(cards, _controls())

        self.assertEqual(card["modelId"], "auto")
        self.assertEqual(card["provider"], "auto")
        self.assertTrue(card["capabilities"]["vision"])
        self.assertEqual(card["autoResolvedModelId"], "deepseek-chat")
        self.assertEqual(card["health"]["status"], "healthy")
        self.assertEqual(card["contextWindowTokens"], 128000)

    def test_auto_card_is_unhealthy_when_nothing_can_be_picked(self):
        with (
            patch("app.api.models.pick_auto_model", return_value=None),
            patch("app.api.models.get_auto_model_candidates", return_value=CANDIDATES),
        ):
            card = _build_auto_card(self._cards(), _controls())

        self.assertEqual(card["health"]["status"], "unhealthy")
        self.assertIsNone(card["autoResolvedModelName"])


if __name__ == "__main__":
    unittest.main()
