import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api.models import _build_auto_card, _entry_to_card
from app.services.auto_model import (
    get_all_auto_model_candidates,
    get_auto_model_candidates,
    get_auto_provider_groups,
    pick_auto_model,
)
from app.services.config_defaults import DEFAULT_AUTO_MODEL_CONFIG

CATALOG = {
    "mimo-v2.6-pro": {"db_model": True, "metadata": {"cost_tier": "mid", "capabilities": {"vision": True}}},
    "deepseek-chat": {"db_model": True, "metadata": {"cost_tier": "low", "capabilities": {"functionCalling": True}}},
    "qwen3.8-max": {
        "db_model": True,
        "metadata": {
            "cost_tier": "high",
            "capabilities": {"vision": True, "functionCalling": True, "searchCapable": True},
        },
    },
    "cheap-extra": {"db_model": True, "metadata": {"cost_tier": "low", "capabilities": {}}},
    "candidate/xiaomi/*": {"db_model": False, "metadata": {}},
}
CANDIDATES = ["mimo-v2.6-pro", "deepseek-chat", "qwen3.8-max"]


def _controls(**by_model):
    repo = MagicMock()
    repo.get_by_model_ids.side_effect = lambda ids: {key: value for key, value in by_model.items() if key in ids}
    return repo


class PickAutoModelTests(unittest.TestCase):
    def _pick(self, *, controls=None, unhealthy=(), catalog=CATALOG, require_vision=False, mode="auto"):
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
            return pick_auto_model(controls or _controls(), require_vision=require_vision, mode=mode)

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

    def test_plan_mode_requires_function_calling(self):
        self.assertEqual(self._pick(mode="plan"), "deepseek-chat")

    def test_deep_research_requires_search_capable_tools(self):
        self.assertEqual(self._pick(mode="deep_research"), "qwen3.8-max")

    def test_mode_requirement_falls_back_to_first_usable_when_no_candidate_qualifies(self):
        # 交给任务策略按原规则报错，不在选择阶段掩盖
        self.assertEqual(self._pick(mode="deep_research", unhealthy={"qwen3.8-max"}), "mimo-v2.6-pro")

    def test_vision_relaxes_before_mode_requirement(self):
        self.assertEqual(
            self._pick(mode="plan", require_vision=True, unhealthy={"qwen3.8-max"}),
            "deepseek-chat",
        )
        self.assertEqual(self._pick(mode="plan", require_vision=True), "qwen3.8-max")

    def test_empty_catalog_trusts_first_candidate(self):
        self.assertEqual(self._pick(catalog={}), "mimo-v2.6-pro")


class AutoProviderGroupTests(unittest.TestCase):
    def _groups(self, payload, mode="auto"):
        with patch("app.services.auto_model.DEFAULT_AUTO_MODEL_CONFIG", payload):
            return get_auto_provider_groups(mode), get_auto_model_candidates(mode)

    def test_default_prefers_flash_then_pro_within_each_provider(self):
        groups, candidates = self._groups(DEFAULT_AUTO_MODEL_CONFIG)
        self.assertEqual([group.provider for group in groups], ["mimo", "deepseek", "qwen"])
        self.assertEqual(
            candidates,
            ["mimo-v2.6-flash", "mimo-v2.6-pro", "deepseek-chat", "deepseek-reasoner", "qwen3.8-flash"],
        )

    def test_default_plan_and_deep_research_prefer_pro_within_each_provider(self):
        for mode in ("plan", "deep_research"):
            with self.subTest(mode=mode):
                _, candidates = self._groups(DEFAULT_AUTO_MODEL_CONFIG, mode)
                self.assertEqual(
                    candidates,
                    ["mimo-v2.6-pro", "mimo-v2.6-flash", "deepseek-reasoner", "deepseek-chat", "qwen3.8-flash"],
                )

    def test_mode_without_own_groups_uses_default_providers(self):
        payload = {
            "providers": [{"provider": "deepseek", "models": ["deepseek-chat"]}],
            "modes": {"plan": {"providers": [{"provider": "mimo", "models": ["mimo-v2.6-pro"]}]}},
        }
        self.assertEqual(self._groups(payload, "plan")[1], ["mimo-v2.6-pro"])
        self.assertEqual(self._groups(payload, "deep_research")[1], ["deepseek-chat"])
        self.assertEqual(self._groups(payload, "auto")[1], ["deepseek-chat"])
        broken = {**payload, "modes": {"plan": {"providers": []}}}
        self.assertEqual(self._groups(broken, "plan")[1], ["deepseek-chat"])

    def test_all_candidates_union_every_mode_in_first_seen_order(self):
        payload = {
            "providers": [{"provider": "deepseek", "models": ["deepseek-chat"]}],
            "modes": {
                "deep_research": {"providers": [{"provider": "qwen", "models": ["qwen3.8-max", "deepseek-chat"]}]}
            },
        }
        with patch("app.services.auto_model.DEFAULT_AUTO_MODEL_CONFIG", payload):
            self.assertEqual(get_all_auto_model_candidates(), ["deepseek-chat", "qwen3.8-max"])

    def test_groups_flatten_in_configured_order(self):
        payload = {
            "providers": [
                {"provider": "deepseek", "models": ["deepseek-chat"]},
                {"provider": "mimo", "models": ["mimo-v2.6-pro", "", 3]},
            ]
        }
        groups, candidates = self._groups(payload)
        self.assertEqual(groups[1].models, ("mimo-v2.6-pro",))
        self.assertEqual(candidates, ["deepseek-chat", "mimo-v2.6-pro"])


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
            patch("app.api.models.get_all_auto_model_candidates", return_value=["deepseek-chat", "qwen3.8-max"]),
        ):
            card = _build_auto_card(cards, _controls())

        self.assertEqual(card["modelId"], "auto")
        self.assertEqual(card["provider"], "auto")
        self.assertIs(card["virtual"], True)
        self.assertTrue(card["capabilities"]["vision"])
        self.assertEqual(card["autoResolvedModelId"], "deepseek-chat")
        self.assertEqual(card["health"]["status"], "healthy")
        self.assertEqual(card["contextWindowTokens"], 128000)

    def test_auto_card_is_unhealthy_when_nothing_can_be_picked(self):
        with (
            patch("app.api.models.pick_auto_model", return_value=None),
            patch("app.api.models.get_all_auto_model_candidates", return_value=CANDIDATES),
        ):
            card = _build_auto_card(self._cards(), _controls())

        self.assertEqual(card["health"]["status"], "unhealthy")
        self.assertIsNone(card["autoResolvedModelName"])


if __name__ == "__main__":
    unittest.main()
