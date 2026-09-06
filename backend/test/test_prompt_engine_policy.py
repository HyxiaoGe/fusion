"""持久引擎策略只前进；数据库保护旧同步器和代码回滚的能力边界。"""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.prompt_bundle import validate_published_bundle
from app.core.prompt_bundle_integrity import PromptBundleRevisionConflict
from test import test_prompt_bundle_hold_migration as hold_migrations
from test.test_prompt_bundle_hold import active_revision, enter, release, versions
from test.test_prompt_engine_bridge import jinja_bundle

POLICY_MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/f5a7d0e3b812_add_prompt_engine_policy.py"
migrated_factory = hold_migrations.migrated_factory


@pytest.fixture
def policy_factory(migrated_factory):
    install_engine_policy(migrated_factory)
    return migrated_factory


def install_engine_policy(migrated_factory):
    spec = importlib.util.spec_from_file_location("engine_policy_migration", POLICY_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with migrated_factory.kw["bind"].begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()


def advance(factory, stage):
    from app.services.prompt_engine_policy import advance_prompt_engine_policy

    return advance_prompt_engine_policy(
        stage,
        expected_revision=active_revision(factory),
        actor="受管发布器",
        reason="独立桥接已核验",
        evidence={
            "coordination": "fusion-dev",
            "workers": ["sha256:" + "a" * 64],
            "rollback_image": "repo@sha256:" + "a" * 64,
        },
        session_factory=factory,
    )


def activate(factory, payload):
    from app.services.prompthub_sync_service import _persist_bundle

    return _persist_bundle(payload, mode="apply", session_factory=factory)


def test_legacy_bridge_jinja_activation_floor_and_old_writer(policy_factory):
    from app.services.prompt_engine_policy import get_prompt_engine_stage

    old, new = versions(policy_factory)
    payload = validate_published_bundle(jinja_bundle())
    with pytest.raises(PromptBundleRevisionConflict):
        activate(policy_factory, payload)
    assert active_revision(policy_factory) == new.revision
    assert advance(policy_factory, "bridge")["stage"] == "bridge"
    activate(policy_factory, payload)
    assert advance(policy_factory, "jinja2")["stage"] == "jinja2"
    with policy_factory() as session:
        assert get_prompt_engine_stage(session) == "jinja2"
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE runtime_config_entries SET is_active=true WHERE version=:revision"),
                {"revision": old.revision},
            )
    assert active_revision(policy_factory) == payload["revision"]


def test_cannot_skip_bridge_or_seal_held_legacy(policy_factory):
    old, new = versions(policy_factory)
    with pytest.raises(ValueError):
        advance(policy_factory, "jinja2")
    advance(policy_factory, "bridge")
    enter(policy_factory, old.revision)
    with pytest.raises(ValueError):
        advance(policy_factory, "jinja2")
    assert active_revision(policy_factory) == old.revision
    release(policy_factory, old.revision)


def test_policy_is_idempotent_and_audit_cannot_be_changed_or_deleted(policy_factory):
    versions(policy_factory)
    advance(policy_factory, "bridge")
    assert advance(policy_factory, "bridge")["idempotent"] is True
    for action in (
        "UPDATE prompt_bundle_engine_transitions SET reason='改写'",
        "DELETE FROM prompt_bundle_engine_transitions",
    ):
        with policy_factory() as session:
            with pytest.raises(DBAPIError):
                session.execute(text(action))
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 1


def test_policy_rejects_stale_active_revision_without_audit(policy_factory):
    from app.services.prompt_engine_policy import advance_prompt_engine_policy

    old, new = versions(policy_factory)
    with pytest.raises(ValueError):
        advance_prompt_engine_policy(
            "bridge",
            expected_revision=old.revision,
            actor="管理员",
            reason="测试",
            evidence={},
            session_factory=policy_factory,
        )
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 0


def test_jinja_stage_allows_only_jinja_hold_targets(policy_factory):
    old, new = versions(policy_factory)
    advance(policy_factory, "bridge")
    payload = validate_published_bundle(jinja_bundle())
    activate(policy_factory, payload)
    advance(policy_factory, "jinja2")
    with pytest.raises(DBAPIError):
        enter(policy_factory, old.revision)
    assert active_revision(policy_factory) == payload["revision"]


def test_unattested_bridge_transition_never_opens_jinja_activation(policy_factory, monkeypatch):
    from app.core.config import settings

    old, new = versions(policy_factory)
    monkeypatch.setattr(settings, "PROMPT_P0_BASELINE_ATTESTED", False)
    with pytest.raises(ValueError, match="attestation"):
        advance(policy_factory, "bridge")
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 0
    assert active_revision(policy_factory) == new.revision


def test_failed_policy_commit_keeps_stage_and_active(policy_factory, monkeypatch):
    old, new = versions(policy_factory)
    session = policy_factory()
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("提交失败")))
    from app.services.prompt_engine_policy import advance_prompt_engine_policy

    with pytest.raises(RuntimeError, match="提交失败"):
        advance_prompt_engine_policy(
            "bridge",
            expected_revision=new.revision,
            actor="管理员",
            reason="测试",
            evidence={},
            session_factory=lambda: session,
        )
    with policy_factory() as check:
        assert check.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 0
    assert active_revision(policy_factory) == new.revision


def test_postgresql_engine_guard_uses_shared_lock_and_read_committed():
    from unittest.mock import patch

    from app.services.prompthub_sync_service import _ADVISORY_LOCK_ID

    spec = importlib.util.spec_from_file_location("postgres_engine_migration", POLICY_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module, "op") as operation:
        operation.get_bind.return_value.dialect.name = "postgresql"
        module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in operation.execute.call_args_list)
    assert "+CREATE" not in sql
    assert f"pg_advisory_xact_lock({_ADVISORY_LOCK_ID})" in sql
    assert "plpgsql VOLATILE" in sql and "read committed" in sql
    assert "BEFORE INSERT OR UPDATE ON runtime_config_entries" in sql
    assert "BEFORE UPDATE OR DELETE ON prompt_bundle_engine_transitions" in sql
    with pytest.raises(RuntimeError, match="保留"):
        module.downgrade()
