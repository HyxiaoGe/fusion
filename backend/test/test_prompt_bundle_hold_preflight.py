"""当前发布器把检查代码注入目标镜像，验证其真实完整包消费能力。"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.prompt_bundle import _load_active_bundle_payload, clear_prompt_bundle_cache
from test import test_prompt_bundle_hold_migration as migrations
from test.test_prompt_bundle_hold import enter, versions

migrated_factory = migrations.migrated_factory
PATH = Path(__file__).resolve().parents[2] / "ops/deploy/prompt-hold-preflight.py"


def preflight(factory):
    assert PATH.is_file(), "缺少目标镜像 hold preflight"
    spec = importlib.util.spec_from_file_location("hold_target_preflight", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clear_prompt_bundle_cache()
    with patch(
        "app.core.prompt_bundle._load_active_bundle_payload",
        side_effect=lambda **kwargs: _load_active_bundle_payload(session_factory=factory, **kwargs),
    ):
        return module.verify_prompt_hold_target(factory)


@pytest.mark.parametrize("held", [True, False])
def test_schema_enabled_always_rejects_v1_runtime_even_before_entering_hold(migrated_factory, held):
    old, new = versions(migrated_factory)
    if held:
        enter(migrated_factory, old.revision)
    with patch("app.core.prompt_bundle_integrity.PROMPT_BUNDLE_STORAGE_KEY", "fusion"):
        with pytest.raises(ValueError, match="v2"):
            preflight(migrated_factory)


def test_held_requires_target_image_real_freeze_to_consume_target(migrated_factory):
    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    with patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", "apply"):
        result = preflight(migrated_factory)
    assert result["state"] == "held"
    assert result["source_kind"] == "prompthub_lkg"
    assert result["revision"] == old.revision


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
@pytest.mark.parametrize("held", [True, False])
def test_disabling_apply_cannot_silently_bypass_hold(migrated_factory, mode, held):
    old, new = versions(migrated_factory)
    if held:
        enter(migrated_factory, old.revision)
    with patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", mode):
        with pytest.raises(ValueError, match="apply"):
            preflight(migrated_factory)


def test_code_defaults_cannot_pass_as_held_bundle(migrated_factory):
    from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
    from app.core.prompt_snapshot import build_bundle_snapshot

    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    with (
        patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", "apply"),
        patch(
            "app.core.prompt_bundle.freeze_prompt_bundle",
            return_value=build_bundle_snapshot(DEFAULT_PROMPT_TEMPLATES, payload=None, classifier_prompt=""),
        ),
    ):
        with pytest.raises(ValueError, match="冻结"):
            preflight(migrated_factory)


def test_real_concatenated_smoke_revalidates_held_target_after_remote_timeout(migrated_factory):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.db.database import SessionLocal
    from test.test_prompt_bundle_hold import active_revision

    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    source = "\n".join(
        (PATH.parent / name).read_text() for name in ("prompt-hold-preflight.py", "prompt-bundle-smoke.py")
    )
    remote = SimpleNamespace(fetch_published_bundle=AsyncMock(side_effect=TimeoutError("隔离远端超时")))
    clear_prompt_bundle_cache()
    with (
        patch.dict(SessionLocal.kw, {"bind": migrated_factory.kw["bind"]}),
        patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", "apply"),
        patch("app.services.prompthub_sync_service._build_client", return_value=remote),
    ):
        with pytest.raises(SystemExit) as result:
            exec(compile(source, "<真实发布脚本拼接>", "exec"), {"__name__": "__main__"})
    clear_prompt_bundle_cache()
    assert result.value.code == 0
    assert active_revision(migrated_factory) == old.revision
    remote.fetch_published_bundle.assert_awaited_once()


def test_new_hold_code_cannot_start_without_its_required_migration(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import RuntimeConfigEntry

    engine = create_engine(f"sqlite:///{tmp_path / 'missing-schema.db'}")
    RuntimeConfigEntry.__table__.create(engine)
    try:
        with pytest.raises(ValueError, match="迁移"):
            preflight(sessionmaker(bind=engine))
    finally:
        engine.dispose()
