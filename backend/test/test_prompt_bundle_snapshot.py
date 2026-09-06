"""本地 Prompt 冻结在单次 Run 内保持不可变。"""

import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from app.ai.prompts.agent_loop import get_app_identity_prompt, get_limit_summary_prompt
from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
from app.core import prompt_bundle
from app.core.prompt_catalog import CATALOG_VERSION, PROMPT_SPECS


def changed_defaults(suffix: str) -> dict[str, str]:
    return {key: value + suffix for key, value in DEFAULT_PROMPT_TEMPLATES.items()}


def test_frozen_getters_keep_original_local_content_after_caller_mutation():
    defaults = changed_defaults("\n版本 A")
    frozen = prompt_bundle.freeze_prompt_bundle(defaults, classifier_prompt="分类 A")
    defaults["app_identity"] = "外部变异"
    newer = prompt_bundle.freeze_prompt_bundle(changed_defaults("\n版本 B"), classifier_prompt="分类 B")

    with prompt_bundle.use_prompt_snapshot(frozen):
        assert get_app_identity_prompt().endswith("\n版本 A")
        assert get_limit_summary_prompt().endswith("\n版本 A")
    with prompt_bundle.use_prompt_snapshot(newer):
        assert get_app_identity_prompt().endswith("\n版本 B")

    assert frozen.source_kind == "code_default"
    assert frozen.source_revision is None
    assert frozen.classifier_prompt == "分类 A"
    with pytest.raises(FrozenInstanceError):
        frozen.effective_revision = "b" * 64


def test_code_default_identity_is_canonical_order_independent_and_byte_sensitive():
    frozen = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    reordered = prompt_bundle.freeze_prompt_bundle(dict(reversed(list(DEFAULT_PROMPT_TEMPLATES.items()))))
    changed = prompt_bundle.freeze_prompt_bundle(changed_defaults("\n"))
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
async def test_concurrent_runs_and_worker_threads_keep_distinct_local_snapshots():
    first = prompt_bundle.freeze_prompt_bundle(changed_defaults(" A"))
    second = prompt_bundle.freeze_prompt_bundle(changed_defaults(" B"))

    async def consume(snapshot):
        with prompt_bundle.use_prompt_snapshot(snapshot):
            await asyncio.sleep(0)
            return await asyncio.to_thread(get_limit_summary_prompt)

    values = await asyncio.gather(consume(first), consume(second))
    assert values[0].endswith(" A")
    assert values[1].endswith(" B")
    assert prompt_bundle.current_prompt_snapshot() is None


@pytest.fixture
def anyio_backend():
    return "asyncio"
