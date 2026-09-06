"""冻结后切包、输入变异和并发均不能改变同一 Run 的模板来源。"""

import asyncio
import hashlib
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from app.ai.prompts.agent_loop import get_app_identity_prompt, get_limit_summary_prompt
from app.core import prompt_bundle
from app.core.prompt_catalog import CATALOG_VERSION, PROMPT_SPECS
from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES


def bundle_payload(revision, suffix):
    return {
        "schema_version": 1,
        "project_slug": "fusion",
        "revision": revision,
        "prompts": {
            spec.key: {
                "slug": spec.slug,
                "version": "1.0.0",
                "content": DEFAULT_PROMPT_TEMPLATES[spec.key] + suffix,
                "content_sha256": hashlib.sha256(
                    (DEFAULT_PROMPT_TEMPLATES[spec.key] + suffix).encode("utf-8")
                ).hexdigest(),
                "variables": list(spec.variables),
            }
            for spec in PROMPT_SPECS
        },
    }


@pytest.fixture(autouse=True)
def apply_mode():
    with (
        patch.object(prompt_bundle.settings, "PROMPTHUB_SYNC_MODE", "apply"),
        patch.object(prompt_bundle.settings, "PROMPT_P0_BASELINE_ATTESTED", True),
    ):
        yield


def test_frozen_getters_keep_a_after_active_switches_to_b_and_payload_is_mutated():
    payload = bundle_payload("a" * 64, "\n版本 A")
    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=payload):
        frozen = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES, classifier_prompt="分类 A")
    payload["prompts"]["app_identity"]["content"] = "外部变异"
    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("b" * 64, "\n版本 B")):
        newer = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES, classifier_prompt="分类 B")
        with prompt_bundle.use_prompt_snapshot(frozen):
            assert get_app_identity_prompt().endswith("\n版本 A")
            assert get_limit_summary_prompt().endswith("\n版本 A")
        with prompt_bundle.use_prompt_snapshot(newer):
            assert get_app_identity_prompt().endswith("\n版本 B")
    assert frozen.source_kind == "prompthub_lkg"
    assert frozen.effective_revision == "a" * 64
    assert frozen.classifier_prompt == "分类 A"
    with pytest.raises(FrozenInstanceError):
        frozen.effective_revision = "b" * 64


def test_corrupt_one_key_falls_back_whole_catalog_without_legacy_or_remote_reads():
    payload = bundle_payload("a" * 64, "\n来自 LKG")
    payload["prompts"]["generate_title"]["content"] = "损坏"
    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=payload):
        frozen = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    assert frozen.source_kind == "code_default"
    assert frozen.source_revision is None
    with (
        patch.object(prompt_bundle, "_load_active_bundle_payload", side_effect=AssertionError("冻结后不得读 DB")),
        prompt_bundle.use_prompt_snapshot(frozen),
    ):
        for spec in PROMPT_SPECS:
            body, metadata = prompt_bundle.resolve_prompt_template_with_metadata(spec.key, "错误 fallback")
            assert body == DEFAULT_PROMPT_TEMPLATES[spec.key]
            assert metadata["source_kind"] == "code_default"
            assert metadata["effective_revision"] == frozen.effective_revision


def test_code_default_identity_is_canonical_order_independent_and_byte_sensitive():
    import json

    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=None):
        frozen = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
        reordered = prompt_bundle.freeze_prompt_bundle(dict(reversed(list(DEFAULT_PROMPT_TEMPLATES.items()))))
        changed_defaults = {**DEFAULT_PROMPT_TEMPLATES, "app_identity": DEFAULT_PROMPT_TEMPLATES["app_identity"] + "\n"}
        changed = prompt_bundle.freeze_prompt_bundle(changed_defaults)
    canonical = json.dumps(
        {
            "catalog_version": CATALOG_VERSION,
            "prompts": [
                {
                    "key": spec.key,
                    "slug": spec.slug,
                    "content_sha256": hashlib.sha256(DEFAULT_PROMPT_TEMPLATES[spec.key].encode("utf-8")).hexdigest(),
                    "variables": list(spec.variables),
                }
                for spec in sorted(PROMPT_SPECS, key=lambda item: item.slug)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert frozen.effective_revision == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert reordered.effective_revision == frozen.effective_revision
    assert changed.effective_revision != frozen.effective_revision


@pytest.mark.anyio
async def test_concurrent_runs_and_worker_threads_keep_distinct_snapshots_and_reset_on_error():
    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("a" * 64, " A")):
        first = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("b" * 64, " B")):
        second = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)

    async def consume(snapshot):
        with prompt_bundle.use_prompt_snapshot(snapshot):
            await asyncio.sleep(0)
            return await asyncio.to_thread(get_limit_summary_prompt)

    with patch.object(prompt_bundle, "_load_active_bundle_payload", side_effect=AssertionError("不得重解析")):
        values = await asyncio.gather(consume(first), consume(second))
        assert values[0].endswith(" A")
        assert values[1].endswith(" B")
        with pytest.raises(ValueError), prompt_bundle.use_prompt_snapshot(first):
            raise ValueError("终止 Run")
        assert prompt_bundle.current_prompt_snapshot() is None


def test_p0_transition_cannot_claim_lkg_identity_for_mixed_effective_content():
    with (
        patch.object(prompt_bundle.settings, "PROMPT_P0_BASELINE_ATTESTED", False),
        patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("a" * 64, " 不同正文")),
        pytest.raises(ValueError, match="P0"),
    ):
        prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)


def test_independent_auxiliary_call_uses_new_bundle_and_records_its_own_identity():
    from app.ai.llm_observability import merge_litellm_kwargs
    from app.ai.prompts.prompt_manager import prompt_manager

    with patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("a" * 64, " A")):
        main_run = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    with (
        prompt_bundle.use_prompt_snapshot(main_run),
        patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("b" * 64, " B")),
    ):
        prompt, metadata = prompt_manager.format_prompt_with_metadata("generate_title", content="自然会话")
        assert prompt.endswith(" B")
        sent = merge_litellm_kwargs("generate_title", prompt_metadata=metadata)["extra_body"]["metadata"]
        assert sent["source_kind"] == "prompthub_lkg"
        assert sent["effective_revision"] == "b" * 64
        assert get_app_identity_prompt().endswith(" A")


@pytest.fixture
def anyio_backend():
    return "asyncio"
