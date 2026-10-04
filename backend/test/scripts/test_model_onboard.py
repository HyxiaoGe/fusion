import json
import tempfile
import unittest
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

    def test_main_requires_both_credentials(self):
        with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "", "LITELLM_API_KEY": "k"}):
            self.assertEqual(model_onboard.main(["list"]), 2)


if __name__ == "__main__":
    unittest.main()
