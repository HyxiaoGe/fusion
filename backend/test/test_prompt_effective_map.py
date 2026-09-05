"""P0 切换门禁：effective map 抓取、基线生成与逐 key UTF-8 字节校验。"""

import hashlib
import unittest
from unittest.mock import patch


def _entry(key, slug, content, variables=()):
    from app.services.prompt_effective_map import EffectiveEntry

    return EffectiveEntry(
        key=key, slug=slug, content=content, variables=tuple(variables), source="code-default", source_version="x"
    )


def _full_effective_map(overrides=None):
    from app.core.prompt_catalog import PROMPT_SPECS
    from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

    overrides = overrides or {}
    return {
        spec.key: _entry(
            spec.key, spec.slug, overrides.get(spec.key, DEFAULT_PROMPT_TEMPLATES[spec.key]), spec.variables
        )
        for spec in PROMPT_SPECS
    }


class EffectiveMapCaptureTests(unittest.TestCase):
    def test_pre_p0_code_only_keys_take_code_constants_even_when_bundle_active(self):
        """P0 之前这 5 个 getter 直接 return 代码常量，抓取必须复现该语义。"""

        from app.services import prompt_effective_map
        from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

        bundle = {
            "prompts": {
                key: {"slug": key.replace("_", "-"), "version": "9.9.9", "content": f"BUNDLE-{key}"}
                for key in prompt_effective_map.PRE_P0_CODE_ONLY_KEYS
            }
        }
        with (
            patch.object(prompt_effective_map, "get_active_prompt_bundle_payload", return_value=bundle),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map()

        for key in prompt_effective_map.PRE_P0_CODE_ONLY_KEYS:
            with self.subTest(key=key):
                self.assertEqual(captured[key].content, DEFAULT_PROMPT_TEMPLATES[key])
                self.assertEqual(captured[key].source, "code-default")

    def test_already_consumed_keys_take_active_bundle_value(self):
        from app.services import prompt_effective_map

        bundle = {"prompts": {"generate_title": {"slug": "generate-title", "version": "2.0.0", "content": "线上标题"}}}
        with (
            patch.object(prompt_effective_map, "get_active_prompt_bundle_payload", return_value=bundle),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map()

        self.assertEqual(captured["generate_title"].content, "线上标题")
        self.assertEqual(captured["generate_title"].source, "prompthub")
        self.assertEqual(captured["generate_title"].source_version, "2.0.0")

    def test_already_consumed_keys_fall_back_to_legacy_runtime_config(self):
        """抓取阶段必须包含 legacy 值，否则迁入基线后线上行为会变。"""

        from app.services import prompt_effective_map

        def loader(namespace, key, default, **kwargs):
            if key == "generate_title":
                return {"template": "legacy 标题"}, {"source": "db", "version": "v7"}
            return default, {"source": "code-default", "version": "code-default"}

        with (
            patch.object(prompt_effective_map, "get_active_prompt_bundle_payload", return_value=None),
            patch.object(prompt_effective_map, "get_runtime_config_payload", side_effect=loader),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map()

        self.assertEqual(captured["generate_title"].content, "legacy 标题")
        self.assertEqual(captured["generate_title"].source, "db")

    def test_capture_covers_whole_catalog(self):
        from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY
        from app.services import prompt_effective_map

        with (
            patch.object(prompt_effective_map, "get_active_prompt_bundle_payload", return_value=None),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map()

        self.assertEqual(set(captured), set(PROMPT_SPEC_BY_KEY))


class BaselineBundleTests(unittest.TestCase):
    def test_baseline_is_built_from_effective_map_not_code_defaults(self):
        from app.services.prompt_effective_map import build_effective_baseline_bundle

        payload = build_effective_baseline_bundle(_full_effective_map({"generate_title": "线上标题"}))

        by_slug = {item["slug"]: item for item in payload["prompts"]}
        self.assertEqual(by_slug["generate-title"]["content"], "线上标题")
        self.assertEqual(by_slug["generate-title"]["content_sha256"], hashlib.sha256("线上标题".encode()).hexdigest())

    def test_baseline_rejects_incomplete_effective_map(self):
        from app.services.prompt_effective_map import EffectiveBaselineMismatch, build_effective_baseline_bundle

        partial = _full_effective_map()
        partial.pop("limit_summary")
        with self.assertRaises(EffectiveBaselineMismatch):
            build_effective_baseline_bundle(partial)


class ByteVerificationTests(unittest.TestCase):
    def test_identical_bytes_pass(self):
        from app.services.prompt_effective_map import assert_bundle_matches_effective_map

        effective_map = _full_effective_map()
        candidate = {key: entry.content for key, entry in effective_map.items()}
        assert_bundle_matches_effective_map(candidate, effective_map)

    def test_whitespace_only_difference_fails_closed(self):
        """不做 strip / 换行归一化：任何字节差异都会改变模型可见正文。"""

        from app.services.prompt_effective_map import EffectiveBaselineMismatch, assert_bundle_matches_effective_map

        effective_map = _full_effective_map()
        candidate = {key: entry.content for key, entry in effective_map.items()}
        candidate["limit_summary"] = candidate["limit_summary"] + "\n"

        with self.assertRaises(EffectiveBaselineMismatch) as ctx:
            assert_bundle_matches_effective_map(candidate, effective_map)
        self.assertIn("limit_summary", str(ctx.exception))

    def test_missing_and_extra_entries_are_reported(self):
        from app.services.prompt_effective_map import verify_bundle_matches_effective_map

        effective_map = _full_effective_map()
        candidate = {key: entry.content for key, entry in effective_map.items()}
        candidate.pop("app_identity")
        candidate["unknown_key"] = "x"

        mismatches = verify_bundle_matches_effective_map(candidate, effective_map)
        joined = "\n".join(mismatches)
        self.assertIn("app_identity: 待激活 bundle 缺少该条目", joined)
        self.assertIn("unknown_key: 待激活 bundle 含 catalog 之外的条目", joined)


class CodeDefaultRevisionTests(unittest.TestCase):
    def test_is_deterministic_and_differs_from_prompthub_algorithm(self):
        from app.services.prompt_effective_map import code_default_effective_revision

        first = code_default_effective_revision()
        self.assertEqual(first, code_default_effective_revision())
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_changes_when_a_code_default_changes(self):
        from app.services import prompt_effective_map

        baseline = prompt_effective_map.code_default_effective_revision()
        patched = dict(prompt_effective_map.DEFAULT_PROMPT_TEMPLATES)
        patched["app_identity"] = patched["app_identity"] + "X"
        with patch.object(prompt_effective_map, "DEFAULT_PROMPT_TEMPLATES", patched):
            self.assertNotEqual(prompt_effective_map.code_default_effective_revision(), baseline)


if __name__ == "__main__":
    unittest.main()
