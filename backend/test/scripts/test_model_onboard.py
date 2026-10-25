import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import model_onboard
from scripts.check_litellm_candidate_preflight import CaseResult, PreflightReport


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeProxy:
    """内存里的代理：/model/info、/model/new、/model/delete、/key/info、/key/update。"""

    def __init__(self, entries=None, key_models=None):
        self.entries = list(entries or [])
        self.key_models = list(key_models or [])
        self.posts = []

    def get(self, url, **kwargs):
        if url.endswith("/model/info"):
            return _Response({"data": self.entries})
        if url.endswith("/key/info"):
            return _Response({"info": {"models": list(self.key_models)}})
        raise AssertionError(url)

    def post(self, url, *, json, **kwargs):
        self.posts.append((url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1], json))
        if url.endswith("/model/new"):
            entry = {**json, "model_info": {**json["model_info"], "id": f"uuid-{json['model_name']}", "db_model": True}}
            self.entries.append(entry)
        elif url.endswith("/model/delete"):
            self.entries = [e for e in self.entries if e["model_info"]["id"] != json["id"]]
        elif url.endswith("/key/update"):
            self.key_models = list(json["models"])
        return _Response({})

    def patch(self, url, *, json, **kwargs):
        model_uuid = url.rsplit("/", 2)[-2]
        self.posts.append(("model/update", json))
        for entry in self.entries:
            if entry["model_info"]["id"] == model_uuid:
                entry["model_info"] = {**entry["model_info"], **json.get("model_info", {})}
                entry["litellm_params"] = {**entry.get("litellm_params", {}), **json.get("litellm_params", {})}
                return _Response({})
        return _Response({"error": "missing"}, status_code=404)


def _add_args(**overrides):
    values = dict(
        model_id="new-model",
        upstream="openai/new-model",
        api_base="https://api.example.com/v1",
        api_key_env="EXAMPLE_API_KEY",
        input_price=0.2,
        output_price=0.8,
        context_window=131072,
        provider_key="example",
        provider_display="示例",
        display_name=None,
        capabilities=["functionCalling", "deepThinking"],
        timeout_seconds=5.0,
        cache_price=None,
        input_price_above_200k=None,
        output_price_above_200k=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _report(candidate, *, passed=True, cost=True):
    status = "passed" if passed else "failed"
    names = ["text", "stream", "tool_calling", "vision", "reasoning", "preserved_tool_round"]
    cases = [
        CaseResult(
            name=name,
            status="skipped" if name == "vision" else status,
            usage_present=True,
            cost_present=cost,
            reasoning_present=True if name == "reasoning" else None,
        )
        for name in names
    ]
    return PreflightReport(healthy=passed, dry_run=False, candidate=candidate, cases=cases)


class ModelOnboardTests(unittest.TestCase):
    def setUp(self):
        self.proxy = FakeProxy(key_models=["deepseek-chat"])
        self.admin = model_onboard.ProxyAdmin(self.proxy, "http://proxy:4000", "master", "fusion-key")

    def test_add_registers_hidden_model_and_preflights_by_alias(self):
        captured = {}

        def fake_preflight(*, candidate, base_url, api_key, apply, client, timeout_seconds):
            captured.update(candidate=candidate, api_key=api_key, apply=apply)
            return _report(candidate)

        with patch.object(model_onboard, "run_preflight", side_effect=fake_preflight):
            result = model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)

        self.assertTrue(result["healthy"])
        self.assertEqual(self.proxy.key_models, ["deepseek-chat"], "登记后不进白名单")
        entry = self.admin.find_model("new-model")
        self.assertEqual(entry["litellm_params"]["api_key"], "os.environ/EXAMPLE_API_KEY")
        self.assertAlmostEqual(entry["litellm_params"]["input_cost_per_token"], 0.2e-6)
        self.assertEqual(entry["model_info"]["max_input_tokens"], 131072)
        metadata = entry["model_info"]["metadata"]
        self.assertEqual(metadata["capabilities"]["functionCalling"], True)
        self.assertEqual(metadata["capabilities"]["vision"], False)
        self.assertEqual(metadata["provider_display"], "示例")
        self.assertEqual(captured["candidate"].preflight_model, "new-model")
        self.assertEqual(captured["api_key"], "master")
        self.assertTrue(captured["apply"])

    def test_add_rolls_back_when_cost_is_missing_even_if_cases_pass(self):
        with patch.object(
            model_onboard, "run_preflight", side_effect=lambda **kw: _report(kw["candidate"], cost=False)
        ):
            result = model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)

        self.assertEqual(result["status"], "preflight_failed_rolled_back")
        self.assertFalse(result["checks"]["cost"])
        self.assertIsNone(self.admin.find_model("new-model"))

    def test_add_refuses_existing_model(self):
        with patch.object(model_onboard, "run_preflight", side_effect=lambda **kw: _report(kw["candidate"])):
            model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)
            with self.assertRaises(model_onboard.OnboardError) as ctx:
                model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)
        self.assertEqual(ctx.exception.code, "model_exists")

    def test_publish_appends_to_allowlist_and_bumps_catalog(self):
        with patch.object(model_onboard, "run_preflight", side_effect=lambda **kw: _report(kw["candidate"])):
            model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)
        with patch.object(model_onboard, "bump_catalog_generation", return_value="7"):
            result = model_onboard.cmd_publish(self.admin, SimpleNamespace(model_id="new-model"))

        self.assertEqual(self.proxy.key_models, ["deepseek-chat", "new-model"])
        self.assertEqual(result["catalog_generation"], "7")

    def test_publish_requires_registered_model(self):
        with self.assertRaises(model_onboard.OnboardError) as ctx:
            model_onboard.cmd_publish(self.admin, SimpleNamespace(model_id="ghost"))
        self.assertEqual(ctx.exception.code, "model_missing")

    def test_retire_refuses_model_still_hardcoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            (root / "uses.py").write_text('VISION_MODEL_ID = "new-model"\n', encoding="utf-8")
            with self.assertRaises(model_onboard.OnboardError) as ctx:
                model_onboard.cmd_retire(self.admin, SimpleNamespace(model_id="new-model"), code_root=root)
        self.assertEqual(ctx.exception.code, "model_referenced_in_code")
        self.assertIn("app/uses.py:1", str(ctx.exception))

    def test_retire_removes_allowlist_and_model_and_returns_backup(self):
        with patch.object(model_onboard, "run_preflight", side_effect=lambda **kw: _report(kw["candidate"])):
            model_onboard.cmd_add(self.admin, _add_args(), client=self.proxy)
        self.proxy.key_models.append("new-model")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(model_onboard, "bump_catalog_generation", return_value="8"),
        ):
            result = model_onboard.cmd_retire(self.admin, SimpleNamespace(model_id="new-model"), code_root=Path(tmp))

        self.assertEqual(self.proxy.key_models, ["deepseek-chat"])
        self.assertIsNone(self.admin.find_model("new-model"))
        self.assertEqual(result["backup"]["model_name"], "new-model")
        self.assertNotIn("master", json.dumps(result))

    def _registered_proxy(self):
        return FakeProxy(
            entries=[
                {
                    "model_name": "m1",
                    "litellm_params": {
                        "model": "openai/m1",
                        "api_base": "https://api.m1.example/v1",
                        "api_key": "os.environ/M1_API_KEY",
                        "input_cost_per_token": 1e-7,
                        "output_cost_per_token": 2e-7,
                    },
                    "model_info": {
                        "id": "uuid-m1",
                        "db_model": True,
                        "max_input_tokens": 1000,
                        "metadata": {
                            "display_name": "M1",
                            "capabilities": {"deepThinking": True, "vision": True},
                            "pricing": {"unit": "USD/1M tokens", "input": 0.1, "output": 0.2},
                        },
                    },
                }
            ]
        )

    def test_set_capabilities_merges_flags_keeps_other_metadata_and_bumps_catalog(self):
        proxy = self._registered_proxy()
        admin = model_onboard.ProxyAdmin(proxy, "http://proxy", "master", "fusion")
        args = SimpleNamespace(model_id="m1", enable=["thinkingSwitchable"], disable=["vision"])

        with patch.object(model_onboard, "bump_catalog_generation", return_value="gen-2"):
            result = model_onboard.cmd_set_capabilities(admin, args)

        self.assertEqual(result["status"], "capabilities_updated")
        self.assertEqual(result["before"], {"deepThinking": True, "vision": True})
        self.assertEqual(
            result["after"],
            {"deepThinking": True, "vision": False, "thinkingSwitchable": True},
        )
        self.assertEqual(result["catalog_generation"], "gen-2")
        info = proxy.entries[0]["model_info"]
        self.assertEqual(info["metadata"]["display_name"], "M1")
        self.assertEqual(info["max_input_tokens"], 1000)
        self.assertEqual(proxy.entries[0]["litellm_params"], self._registered_proxy().entries[0]["litellm_params"])

    def test_set_capabilities_requires_registered_model(self):
        admin = model_onboard.ProxyAdmin(FakeProxy(), "http://proxy", "master", "fusion")
        args = SimpleNamespace(model_id="missing", enable=["thinkingSwitchable"], disable=[])

        with self.assertRaises(model_onboard.OnboardError) as raised:
            model_onboard.cmd_set_capabilities(admin, args)

        self.assertEqual(raised.exception.code, "model_missing")

    def test_set_capabilities_parser_rejects_unknown_capability(self):
        parser = model_onboard.build_parser()
        parsed = parser.parse_args(["set-capabilities", "m1", "--enable", "thinkingSwitchable"])
        self.assertEqual(parsed.enable, ["thinkingSwitchable"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["set-capabilities", "m1", "--enable", "unknown"])

    def test_set_context_window_updates_window_keeps_metadata_and_bumps_catalog(self):
        proxy = self._registered_proxy()
        admin = model_onboard.ProxyAdmin(proxy, "http://proxy", "master", "fusion")
        args = model_onboard.build_parser().parse_args(["set-context-window", "m1", "991808"])

        with patch.object(model_onboard, "bump_catalog_generation", return_value="gen-3"):
            result = model_onboard.cmd_set_context_window(admin, args)

        self.assertEqual(result["status"], "context_window_updated")
        self.assertEqual((result["before"], result["after"]), (1000, 991808))
        self.assertEqual(result["catalog_generation"], "gen-3")
        self.assertEqual(proxy.posts[-1], ("model/update", {"model_info": {"max_input_tokens": 991808}}))
        info = proxy.entries[0]["model_info"]
        self.assertEqual(info["max_input_tokens"], 991808)
        self.assertEqual(info["metadata"]["capabilities"], {"deepThinking": True, "vision": True})

    def test_set_context_window_rejects_non_positive_and_missing_model(self):
        admin = model_onboard.ProxyAdmin(self._registered_proxy(), "http://proxy", "master", "fusion")
        for model_id, tokens, code in (("m1", 0, "invalid_context_window"), ("missing", 1000, "model_missing")):
            with self.subTest(code=code), self.assertRaises(model_onboard.OnboardError) as raised:
                model_onboard.cmd_set_context_window(admin, SimpleNamespace(model_id=model_id, tokens=tokens))
            self.assertEqual(raised.exception.code, code)

    def test_add_writes_cache_price_to_billing_fields_and_pricing_metadata(self):
        with patch.object(model_onboard, "run_preflight", side_effect=lambda **kw: _report(kw["candidate"])):
            model_onboard.cmd_add(self.admin, _add_args(cache_price=0.02), client=self.proxy)

        entry = self.admin.find_model("new-model")
        self.assertAlmostEqual(entry["litellm_params"]["cache_read_input_token_cost"], 0.02e-6)
        self.assertAlmostEqual(entry["model_info"]["cache_read_input_token_cost"], 0.02e-6)
        self.assertEqual(
            entry["model_info"]["metadata"]["pricing"],
            {"unit": "USD/1M tokens", "input": 0.2, "output": 0.8, "cache_read_input": 0.02},
        )
        self.assertNotIn("input_cost_per_token_above_200k_tokens", entry["litellm_params"], "没传的档位不写")

    def test_set_pricing_updates_billing_and_catalog_fields_keeps_route_and_bumps_catalog(self):
        proxy = self._registered_proxy()
        admin = model_onboard.ProxyAdmin(proxy, "http://proxy", "master", "fusion")
        args = model_onboard.build_parser().parse_args(
            ["set-pricing", "m1", "--input-price", "0.3", "--output-price", "1.2", "--cache-price", "0.006"]
        )

        with patch.object(model_onboard, "bump_catalog_generation", return_value="gen-4"):
            result = model_onboard.cmd_set_pricing(admin, args)

        self.assertEqual(result["status"], "pricing_updated")
        self.assertEqual(result["catalog_generation"], "gen-4")
        self.assertEqual(result["before"]["input_cost_per_token"], 1e-7)
        self.assertIsNone(result["before"]["cache_read_input_token_cost"])
        self.assertAlmostEqual(result["after"]["output_cost_per_token"], 1.2e-6)
        params = proxy.entries[0]["litellm_params"]
        info = proxy.entries[0]["model_info"]
        self.assertAlmostEqual(params["input_cost_per_token"], 0.3e-6)
        self.assertAlmostEqual(params["cache_read_input_token_cost"], 0.006e-6)
        self.assertEqual((params["model"], params["api_base"]), ("openai/m1", "https://api.m1.example/v1"))
        self.assertAlmostEqual(info["input_cost_per_token"], 0.3e-6, msg="Fusion 目录读 model_info")
        self.assertEqual(info["max_input_tokens"], 1000)
        self.assertEqual(info["metadata"]["capabilities"], {"deepThinking": True, "vision": True})
        self.assertEqual(
            info["metadata"]["pricing"],
            {"unit": "USD/1M tokens", "input": 0.3, "output": 1.2, "cache_read_input": 0.006},
        )
        self.assertNotIn("input_cost_per_token_above_200k_tokens", params, "没传的分档不写")

    def test_set_pricing_writes_200k_tiers_when_given(self):
        proxy = self._registered_proxy()
        admin = model_onboard.ProxyAdmin(proxy, "http://proxy", "master", "fusion")
        args = model_onboard.build_parser().parse_args(
            [
                "set-pricing",
                "m1",
                "--input-price",
                "2",
                "--output-price",
                "6",
                "--cache-price",
                "0.5",
                "--input-price-above-200k",
                "4",
                "--output-price-above-200k",
                "12",
            ]
        )
        with patch.object(model_onboard, "bump_catalog_generation", return_value="gen-5"):
            result = model_onboard.cmd_set_pricing(admin, args)

        params = proxy.entries[0]["litellm_params"]
        self.assertAlmostEqual(params["input_cost_per_token_above_200k_tokens"], 4e-6)
        self.assertAlmostEqual(params["output_cost_per_token_above_200k_tokens"], 12e-6)
        self.assertEqual(result["pricing"]["input_above_200k"], 4)

    def test_set_pricing_rejects_missing_model_negative_price_and_failed_readback(self):
        proxy = self._registered_proxy()
        admin = model_onboard.ProxyAdmin(proxy, "http://proxy", "master", "fusion")
        for model_id, cache_price, code in (("missing", None, "model_missing"), ("m1", -1.0, "invalid_price")):
            with self.subTest(code=code), self.assertRaises(model_onboard.OnboardError) as raised:
                model_onboard.cmd_set_pricing(
                    admin,
                    SimpleNamespace(
                        model_id=model_id,
                        input_price=1.0,
                        output_price=2.0,
                        cache_price=cache_price,
                        input_price_above_200k=None,
                        output_price_above_200k=None,
                    ),
                )
            self.assertEqual(raised.exception.code, code)

        # 代理回读时 litellm_params 没变（PATCH 未生效）→ 报回读失败而不是静默成功
        with patch.object(FakeProxy, "patch", lambda self, url, *, json, **kw: _Response({})):
            args = model_onboard.build_parser().parse_args(
                ["set-pricing", "m1", "--input-price", "1", "--output-price", "2"]
            )
            with self.assertRaises(model_onboard.OnboardError) as raised:
                model_onboard.cmd_set_pricing(admin, args)
        self.assertEqual(raised.exception.code, "model_update_readback_failed")

    def test_main_requires_both_credentials(self):
        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "", "LITELLM_API_KEY": "k"}):
            self.assertEqual(model_onboard.main(["list"]), 2)


if __name__ == "__main__":
    unittest.main()
