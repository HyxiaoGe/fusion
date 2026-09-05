"""P0 切换门禁：effective map 抓取、基线生成与逐 key UTF-8 字节校验。"""

import hashlib
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


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
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value=bundle),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

        for key in prompt_effective_map.PRE_P0_CODE_ONLY_KEYS:
            with self.subTest(key=key):
                self.assertEqual(captured[key].content, DEFAULT_PROMPT_TEMPLATES[key])
                self.assertEqual(captured[key].source, "code-default")

    def test_already_consumed_keys_take_active_bundle_value(self):
        from app.services import prompt_effective_map

        bundle = {"prompts": {"generate_title": {"slug": "generate-title", "version": "2.0.0", "content": "线上标题"}}}
        with (
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value=bundle),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

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
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value=None),
            patch.object(prompt_effective_map, "get_runtime_config_payload", side_effect=loader),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

        self.assertEqual(captured["generate_title"].content, "legacy 标题")
        self.assertEqual(captured["generate_title"].source, "db")

    def test_capture_covers_whole_catalog(self):
        from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY
        from app.services import prompt_effective_map

        with (
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value=None),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

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


class P0TransitionGateTests(unittest.TestCase):
    """门禁必须不可绕过：激活与 apply 启动两条路径都拦得住。"""

    def _bundle_prompts(self, overrides=None):
        from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

        prompts = dict(DEFAULT_PROMPT_TEMPLATES)
        prompts.update(overrides or {})
        return prompts

    def test_attested_claim_passes_when_bundle_matches_code_defaults(self):
        from app.services.prompt_effective_map import assert_p0_transition_gate

        with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
            assert_p0_transition_gate(self._bundle_prompts())

    def test_attested_claim_is_rejected_when_bundle_differs(self):
        """attested 是运维声明；声明与事实不符必须 fail closed。"""

        from app.services.prompt_effective_map import EffectiveBaselineMismatch, assert_p0_transition_gate

        with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
            with self.assertRaises(EffectiveBaselineMismatch) as ctx:
                assert_p0_transition_gate(self._bundle_prompts({"app_identity": "改过的身份规则"}))
        self.assertIn("app_identity", str(ctx.exception))

    def test_unattested_does_not_block_publishing_new_baseline(self):
        """过渡期这些 key 被钉住，bundle 内容不参与消费，拦截反而会挡住发新基线。"""

        from app.services.prompt_effective_map import assert_p0_transition_gate

        with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", False):
            assert_p0_transition_gate(self._bundle_prompts({"app_identity": "尚未生效的新正文"}))

    def test_activation_is_blocked_before_any_row_is_written(self):
        """attested 下门禁失败必须真正阻止激活：不写行、不改 LKG。"""

        from app.services import prompthub_sync_service
        from app.services.prompt_effective_map import EffectiveBaselineMismatch

        payload = {
            "schema_version": 1,
            "project_slug": "fusion",
            "revision": "d" * 64,
            "prompts": {
                key: {"slug": key.replace("_", "-"), "version": "1.0.0", "content": content}
                for key, content in self._bundle_prompts({"app_identity": "被篡改"}).items()
            },
        }
        session_factory = unittest.mock.Mock()

        with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
            with self.assertRaises(EffectiveBaselineMismatch):
                prompthub_sync_service._persist_bundle(payload, mode="apply", session_factory=session_factory)

        session_factory.return_value.add.assert_not_called()
        session_factory.return_value.commit.assert_not_called()

    def test_apply_startup_fails_fast_on_ungated_active_bundle(self):
        """首次部署时库里可能已有从未过门禁的 active bundle，启动必须拦住。"""

        from app.services import prompt_catalog_integrity
        from app.services.prompt_effective_map import EffectiveBaselineMismatch

        payload = {
            "prompts": {
                key: {"content": content}
                for key, content in self._bundle_prompts({"tool_usage_contract": "被篡改"}).items()
            }
        }
        with (
            patch("app.services.prompt_catalog_integrity.settings.PROMPTHUB_SYNC_MODE", "apply"),
            patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True),
            patch.object(prompt_catalog_integrity, "get_active_prompt_bundle_payload", return_value=payload),
        ):
            with self.assertRaises(EffectiveBaselineMismatch):
                prompt_catalog_integrity.verify_p0_baseline_gate()

    def test_non_apply_startup_skips_gate(self):
        from app.services import prompt_catalog_integrity

        with patch("app.services.prompt_catalog_integrity.settings.PROMPTHUB_SYNC_MODE", "disabled"):
            prompt_catalog_integrity.verify_p0_baseline_gate()

    def test_apply_unattested_without_active_bundle_fails_closed(self):
        """无有效 LKG 是正常可达状态，不是跳过门禁的理由。

        P0 之前 bundle 未命中会回落 legacy prompt_template；本 PR 删除该回落后
        同样情形直接落到代码默认值，因此未经基线校验就启动会改变模型可见正文。
        """

        from app.services import prompt_catalog_integrity
        from app.services.prompt_effective_map import EffectiveBaselineMismatch

        with (
            patch("app.services.prompt_catalog_integrity.settings.PROMPTHUB_SYNC_MODE", "apply"),
            patch("app.services.prompt_catalog_integrity.settings.PROMPT_P0_BASELINE_ATTESTED", False),
            patch.object(prompt_catalog_integrity, "get_active_prompt_bundle_payload", return_value=None),
        ):
            with self.assertRaises(EffectiveBaselineMismatch) as ctx:
                prompt_catalog_integrity.verify_p0_baseline_gate()

        self.assertIn("没有可校验的有效 LKG", str(ctx.exception))


class CaptureIsIndependentOfCurrentSyncModeTests(unittest.TestCase):
    """抓取必须读实际 stored LKG，不能被本进程的消费模式开关左右。"""

    def test_apply_capture_reads_stored_lkg_even_when_process_mode_is_disabled(self):
        from app.services import prompt_effective_map

        bundle = {"prompts": {"generate_title": {"slug": "generate-title", "version": "3.0.0", "content": "线上标题"}}}
        with (
            patch("app.services.prompt_effective_map.settings.PROMPTHUB_SYNC_MODE", "disabled"),
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value=bundle),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

        self.assertEqual(captured["generate_title"].content, "线上标题")
        self.assertEqual(captured["generate_title"].source, "prompthub")

    def test_non_apply_capture_does_not_consult_stored_lkg(self):
        """目标环境本就不是 apply 时，P0 之前不会命中 bundle，抓取也不该命中。"""

        from app.services import prompt_effective_map

        loader = unittest.mock.Mock(return_value={"prompts": {}})
        with (
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", loader),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            prompt_effective_map.capture_pre_p0_effective_map(sync_mode="shadow")

        loader.assert_not_called()

    def test_cli_requires_explicit_sync_mode(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "verify_p0_effective_baseline", ROOT / "scripts" / "verify_p0_effective_baseline.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with self.assertRaises(SystemExit):
            module.main(["capture", "--out", "/tmp/x.json"])


class DisabledWindowEliminationTests(unittest.TestCase):
    """候选代码必须能直接在 apply 模式部署，不经过会改变其余 key 的 disabled 窗口。"""

    def test_transition_pins_code_only_keys_to_code_defaults_even_in_apply(self):
        from app.core import prompt_bundle
        from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

        bundle = {
            "schema_version": 1,
            "project_slug": "fusion",
            "revision": "e" * 64,
            "prompts": {
                "app_identity": {
                    "slug": "app-identity",
                    "version": "9.9.9",
                    "content": "BUNDLE 身份",
                    "content_sha256": hashlib.sha256("BUNDLE 身份".encode()).hexdigest(),
                }
            },
        }
        with (
            patch("app.core.prompt_bundle.settings.PROMPTHUB_SYNC_MODE", "apply"),
            patch("app.core.prompt_bundle.settings.PROMPT_P0_BASELINE_ATTESTED", False),
            patch("app.core.prompt_bundle._load_active_bundle_payload", return_value=bundle),
        ):
            content, metadata = prompt_bundle.resolve_prompt_template_with_metadata(
                "app_identity", DEFAULT_PROMPT_TEMPLATES["app_identity"]
            )

        self.assertEqual(content, DEFAULT_PROMPT_TEMPLATES["app_identity"])
        self.assertEqual(metadata["source"], "code-default-p0-transition")

    def test_other_keys_keep_consuming_bundle_during_transition(self):
        """其余 key 在过渡期仍从 bundle 取值——这正是不能走 disabled 的原因。"""

        from app.core import prompt_bundle

        bundle = {
            "schema_version": 1,
            "project_slug": "fusion",
            "revision": "e" * 64,
            "prompts": {
                "limit_summary": {
                    "slug": "limit-summary",
                    "version": "1.0.0",
                    "content": "线上触顶总结",
                    "content_sha256": hashlib.sha256("线上触顶总结".encode()).hexdigest(),
                }
            },
        }
        with (
            patch("app.core.prompt_bundle.settings.PROMPTHUB_SYNC_MODE", "apply"),
            patch("app.core.prompt_bundle.settings.PROMPT_P0_BASELINE_ATTESTED", False),
            patch("app.core.prompt_bundle._load_active_bundle_payload", return_value=bundle),
        ):
            content, _ = prompt_bundle.resolve_prompt_template_with_metadata("limit_summary", "代码默认值")

        self.assertEqual(content, "线上触顶总结")

    def test_attested_lets_code_only_keys_consume_bundle(self):
        from app.core import prompt_bundle

        bundle = {
            "schema_version": 1,
            "project_slug": "fusion",
            "revision": "e" * 64,
            "prompts": {
                "app_identity": {
                    "slug": "app-identity",
                    "version": "9.9.9",
                    "content": "BUNDLE 身份",
                    "content_sha256": hashlib.sha256("BUNDLE 身份".encode()).hexdigest(),
                }
            },
        }
        with (
            patch("app.core.prompt_bundle.settings.PROMPTHUB_SYNC_MODE", "apply"),
            patch("app.core.prompt_bundle.settings.PROMPT_P0_BASELINE_ATTESTED", True),
            patch("app.core.prompt_bundle._load_active_bundle_payload", return_value=bundle),
        ):
            content, _ = prompt_bundle.resolve_prompt_template_with_metadata("app_identity", "代码默认值")

        self.assertEqual(content, "BUNDLE 身份")

    def test_legacy_marker_accepted_only_before_attestation(self):
        """过渡前发布的 bundle 必须仍能通过校验，否则整包被拒会让全部 key 回落默认值。"""

        from app.core import prompt_bundle
        from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY

        spec = PROMPT_SPEC_BY_KEY["file_content_enhancement"]
        legacy_content = "用户问题: {query}\n\n参考以下文件内容:\n{file_content}"

        with patch("app.core.prompt_bundle.settings.PROMPT_P0_BASELINE_ATTESTED", False):
            self.assertTrue(prompt_bundle._marker_is_acceptable(legacy_content, spec))
        with patch("app.core.prompt_bundle.settings.PROMPT_P0_BASELINE_ATTESTED", True):
            self.assertFalse(prompt_bundle._marker_is_acceptable(legacy_content, spec))


class MigrationCriterionTests(unittest.TestCase):
    """迁移判据必须是逐 key 字节相等，不能用 captured_source。"""

    def test_source_labels_cannot_serve_as_criterion(self):
        from app.core.prompt_catalog import PRE_P0_CODE_ONLY_KEYS
        from app.services import prompt_effective_map

        with (
            patch.object(prompt_effective_map, "load_stored_active_bundle_payload", return_value={"prompts": {}}),
            patch.object(prompt_effective_map, "get_runtime_config_payload", return_value=({}, {})),
        ):
            captured = prompt_effective_map.capture_pre_p0_effective_map(sync_mode="apply")

        # 这些 key 按构造就是 code-default，「全部为 prompthub」在逻辑上不可能成立。
        for key in PRE_P0_CODE_ONLY_KEYS:
            self.assertEqual(captured[key].source, "code-default")

    def test_diff_reports_byte_level_mismatch(self):
        from app.services.prompt_effective_map import diff_effective_maps

        baseline = _full_effective_map()
        candidate = _full_effective_map({"limit_summary": baseline["limit_summary"].content + "\n"})

        mismatches = diff_effective_maps(baseline, candidate)

        self.assertEqual(len(mismatches), 1)
        self.assertIn("limit_summary", mismatches[0])

    def test_identical_maps_report_no_mismatch(self):
        from app.services.prompt_effective_map import diff_effective_maps

        self.assertEqual(diff_effective_maps(_full_effective_map(), _full_effective_map()), [])
