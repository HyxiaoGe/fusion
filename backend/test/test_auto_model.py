import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.ai.litellm_catalog import normalize_capabilities
from app.api.models import _build_auto_card, _entry_to_card
from app.services.auto_model import (
    get_all_auto_model_candidates,
    get_auto_model_candidates,
    get_auto_provider_groups,
    pick_auto_model,
    pick_fallback_model,
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
    def _pick(self, *, controls=None, unhealthy=(), catalog=CATALOG, require_vision=False, mode="auto", allowed=None):
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
            return pick_auto_model(
                controls or _controls(), require_vision=require_vision, mode=mode, allowed_model_ids=allowed
            )

    def test_picks_first_configured_candidate(self):
        self.assertEqual(self._pick(), "mimo-v2.6-pro")

    def test_skips_unhealthy_hidden_and_non_routable_candidates(self):
        controls = _controls(**{"deepseek-chat": SimpleNamespace(selectable=False, routable=True)})
        self.assertEqual(self._pick(controls=controls, unhealthy={"mimo-v2.6-pro"}), "qwen3.8-max")
        controls = _controls(**{"mimo-v2.6-pro": SimpleNamespace(selectable=True, routable=False)})
        self.assertEqual(self._pick(controls=controls), "deepseek-chat")

    def test_open_circuit_candidate_moves_behind_others_but_stays_as_last_resort(self):
        from app.ai.model_circuit_breaker import model_circuit_breaker

        for _ in range(model_circuit_breaker.failure_threshold):
            model_circuit_breaker.record_failure("mimo-v2.6-pro", "first_output_timeout")
        self.assertEqual(self._pick(), "deepseek-chat")
        self.assertEqual(self._pick(allowed=("mimo-v2.6-pro",)), "mimo-v2.6-pro")

    def test_unregistered_candidate_is_skipped(self):
        catalog = {key: value for key, value in CATALOG.items() if key != "mimo-v2.6-pro"}
        self.assertEqual(self._pick(catalog=catalog), "deepseek-chat")

    def test_falls_back_to_rest_of_catalog_by_cost_tier_when_all_candidates_unusable(self):
        self.assertEqual(self._pick(unhealthy=set(CANDIDATES)), "cheap-extra")

    def test_allowed_models_restrict_candidates_without_catalog_fallback(self):
        allowed = ("deepseek-chat", "cheap-extra")
        self.assertEqual(self._pick(allowed=allowed), "deepseek-chat")
        self.assertEqual(self._pick(allowed=allowed, unhealthy={"deepseek-chat"}), "cheap-extra")
        self.assertIsNone(self._pick(allowed=allowed, unhealthy=set(allowed)))
        self.assertEqual(self._pick(allowed=allowed, catalog={}), "deepseek-chat")

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

    def test_mode_requirement_falls_back_to_first_usable_when_no_candidate_qualifies(self):
        # 交给任务策略按原规则报错，不在选择阶段掩盖
        self.assertEqual(self._pick(mode="plan", unhealthy={"deepseek-chat", "qwen3.8-max"}), "mimo-v2.6-pro")

    def test_vision_relaxes_before_mode_requirement(self):
        self.assertEqual(
            self._pick(mode="plan", require_vision=True, unhealthy={"qwen3.8-max"}),
            "deepseek-chat",
        )
        self.assertEqual(self._pick(mode="plan", require_vision=True), "qwen3.8-max")

    def test_empty_catalog_trusts_first_candidate(self):
        self.assertEqual(self._pick(catalog={}), "mimo-v2.6-pro")


def _served_by(provider_key, entry, underlying="openai/x"):
    return {**entry, "underlying": underlying, "metadata": {**entry["metadata"], "provider_key": provider_key}}


FALLBACK_CATALOG = {
    "mimo-v2.6-pro": _served_by("xiaomi", CATALOG["mimo-v2.6-pro"]),
    "deepseek-chat": _served_by("deepseek", CATALOG["deepseek-chat"], underlying="deepseek/deepseek-flash"),
    "deepseek-reasoner": _served_by(
        "deepseek",
        {"db_model": True, "metadata": {"cost_tier": "mid", "capabilities": {"functionCalling": True}}},
        underlying="deepseek/deepseek-reasoner",
    ),
    "qwen3.8-max": _served_by("qwen", CATALOG["qwen3.8-max"]),
    "cheap-extra": _served_by("other", CATALOG["cheap-extra"]),
    "gemini-4-argon": _served_by(
        "google", {"db_model": True, "metadata": {"capabilities": {"vision": True}}}, underlying="gemini/x"
    ),
}


class PickFallbackModelTests(unittest.TestCase):
    def _pick(self, *, failed="gemini-4-argon", required=("agentTools",), controls=None, unhealthy=(), **kwargs):
        catalog = kwargs.pop("catalog", FALLBACK_CATALOG)

        def capabilities(alias, **_kwargs):
            return normalize_capabilities(
                alias, catalog[alias]["metadata"].get("capabilities"), agent_tools_disabled_aliases=set()
            )

        with (
            patch("app.services.auto_model.litellm_catalog.list_aliases", return_value=dict(catalog)),
            patch("app.services.auto_model.litellm_catalog.get_capabilities", side_effect=capabilities),
            patch(
                "app.services.auto_model.litellm_health.get_health",
                side_effect=lambda alias: {"status": "unhealthy" if alias in unhealthy else "healthy"},
            ),
            patch("app.services.auto_model.get_auto_model_candidates", return_value=list(CANDIDATES)),
        ):
            return pick_fallback_model(
                controls,
                failed_model_id=failed,
                preferred_model_id="deepseek-chat",
                required_capabilities=required,
                **kwargs,
            )

    def test_prefers_preferred_model_when_it_has_required_capabilities(self):
        self.assertEqual(self._pick(), "deepseek-chat")

    def test_skips_models_served_by_the_failed_platform(self):
        # 原模型是 deepseek-reasoner：同一平台的 deepseek-chat 不换，mimo-v2.6-pro 不能调工具
        self.assertEqual(self._pick(failed="deepseek-reasoner"), "qwen3.8-max")
        # 都走 OpenAI 兼容接口（底层前缀都是 openai）但平台不同，不算同一家
        self.assertEqual(self._pick(failed="mimo-v2.6-pro", required=("vision",)), "qwen3.8-max")
        # 同一平台上的其他厂商模型（百炼上的 GLM）也跳过
        catalog = {
            **FALLBACK_CATALOG,
            "glm-5.3": _served_by(
                "qwen", {"db_model": True, "metadata": {"cost_tier": "low", "capabilities": {"vision": True}}}
            ),
        }
        self.assertEqual(
            self._pick(failed="qwen3.8-max", required=("vision",), catalog=catalog, unhealthy={"mimo-v2.6-pro"}),
            "gemini-4-argon",
        )

    def test_required_capabilities_are_never_relaxed(self):
        self.assertEqual(self._pick(required=("vision",)), "mimo-v2.6-pro")
        self.assertEqual(self._pick(required=("agentTools", "vision")), "qwen3.8-max")
        self.assertIsNone(self._pick(required=("agentTools", "vision"), unhealthy={"qwen3.8-max"}))

    def test_skips_open_circuit_and_admin_disabled_models(self):
        from app.ai.model_circuit_breaker import model_circuit_breaker

        for _ in range(model_circuit_breaker.failure_threshold):
            model_circuit_breaker.record_failure("deepseek-chat", "http_500")
        self.assertEqual(self._pick(), "qwen3.8-max")
        controls = _controls(**{"qwen3.8-max": SimpleNamespace(selectable=True, routable=False)})
        self.assertEqual(self._pick(controls=controls), "deepseek-reasoner")

    def test_guest_only_picks_from_allowed_models(self):
        allowed = ("mimo-v2.6-pro", "deepseek-chat")
        self.assertEqual(self._pick(allowed_model_ids=allowed), "deepseek-chat")
        self.assertIsNone(self._pick(allowed_model_ids=allowed, unhealthy={"deepseek-chat"}))

    def test_empty_catalog_does_not_switch(self):
        self.assertIsNone(self._pick(catalog={}))


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

    def test_default_plan_prefers_pro_within_each_provider(self):
        _, candidates = self._groups(DEFAULT_AUTO_MODEL_CONFIG, "plan")
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
        self.assertEqual(self._groups(payload, "auto")[1], ["deepseek-chat"])
        broken = {**payload, "modes": {"plan": {"providers": []}}}
        self.assertEqual(self._groups(broken, "plan")[1], ["deepseek-chat"])

    def test_all_candidates_union_every_mode_in_first_seen_order(self):
        payload = {
            "providers": [{"provider": "deepseek", "models": ["deepseek-chat"]}],
            "modes": {"plan": {"providers": [{"provider": "qwen", "models": ["qwen3.8-max", "deepseek-chat"]}]}},
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
