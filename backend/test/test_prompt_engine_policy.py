"""持久引擎策略只前进；数据库保护旧同步器和代码回滚的能力边界。"""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test import test_prompt_bundle_hold_migration as hold_migrations
from test.test_prompt_bundle_hold import active_revision, enter, versions

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


def seed_engine_stage(factory, stage="jinja2"):
    """仅测试夹具：复现由独立桥接版本已提交的阶段事实，不代表真实环境验收。"""
    from app.db.models import PromptBundleEngineTransition

    with factory.begin() as session:
        for value in ("bridge", "jinja2") if stage == "jinja2" else ("bridge",):
            session.add(
                PromptBundleEngineTransition(
                    project_slug="fusion",
                    stage=value,
                    revision="a" * 64,
                    actor="桥接夹具",
                    reason="独立桥接已完成的测试状态",
                    evidence={},
                )
            )


def test_final_database_rejects_old_writer_and_old_hold(policy_factory):
    from app.schemas.response import ApiException
    from test.test_prompt_bundle_v2_transactions import add_bundle
    from test.test_prompt_jinja_only import legacy_contract

    seed_engine_stage(policy_factory)
    old, new = versions(policy_factory)
    legacy = legacy_contract()["payload"]
    add_bundle(policy_factory, legacy, active=False)
    with policy_factory() as session, pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE runtime_config_entries SET is_active=true WHERE version=:revision"),
            {"revision": legacy["revision"]},
        )
    with pytest.raises(ApiException):
        enter(policy_factory, legacy["revision"])
    assert active_revision(policy_factory) == new.revision
    enter(policy_factory, old.revision)
    assert active_revision(policy_factory) == old.revision


def test_final_startup_cannot_skip_persistent_bridge_or_sealing(policy_factory):
    from app.services.prompt_engine_policy import verify_final_prompt_engine_stage

    with pytest.raises(ValueError):
        verify_final_prompt_engine_stage(session_factory=policy_factory)
    seed_engine_stage(policy_factory, "bridge")
    with pytest.raises(ValueError):
        verify_final_prompt_engine_stage(session_factory=policy_factory)
    versions(policy_factory)
    advance(policy_factory, "jinja2")
    verify_final_prompt_engine_stage(session_factory=policy_factory)


def test_policy_is_idempotent_monotonic_and_audit_immutable(policy_factory):
    seed_engine_stage(policy_factory)
    versions(policy_factory)
    assert advance(policy_factory, "jinja2")["idempotent"] is True
    with pytest.raises(ValueError):
        advance(policy_factory, "bridge")
    for action in (
        "UPDATE prompt_bundle_engine_transitions SET reason='改写'",
        "DELETE FROM prompt_bundle_engine_transitions",
    ):
        with policy_factory() as session, pytest.raises(DBAPIError):
            session.execute(text(action))
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 2


def test_policy_rejects_stale_revision_without_adding_final_event(policy_factory):
    from app.services.prompt_engine_policy import advance_prompt_engine_policy

    seed_engine_stage(policy_factory, "bridge")
    old, new = versions(policy_factory)
    with pytest.raises(ValueError):
        advance_prompt_engine_policy(
            "jinja2",
            expected_revision=old.revision,
            actor="管理员",
            reason="测试",
            evidence={},
            session_factory=policy_factory,
        )
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 1


def test_unattested_sealing_does_not_change_active_or_history(policy_factory, monkeypatch):
    from app.core.config import settings

    seed_engine_stage(policy_factory, "bridge")
    old, new = versions(policy_factory)
    monkeypatch.setattr(settings, "PROMPT_P0_BASELINE_ATTESTED", False)
    with pytest.raises(ValueError, match="attestation"):
        advance(policy_factory, "jinja2")
    with policy_factory() as session:
        assert session.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 1
    assert active_revision(policy_factory) == new.revision


def test_failed_policy_commit_keeps_stage_and_active(policy_factory, monkeypatch):
    from app.services.prompt_engine_policy import advance_prompt_engine_policy

    seed_engine_stage(policy_factory, "bridge")
    old, new = versions(policy_factory)
    session = policy_factory()
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("提交失败")))
    with pytest.raises(RuntimeError, match="提交失败"):
        advance_prompt_engine_policy(
            "jinja2",
            expected_revision=new.revision,
            actor="管理员",
            reason="测试",
            evidence={},
            session_factory=lambda: session,
        )
    with policy_factory() as check:
        assert check.execute(text("SELECT count(*) FROM prompt_bundle_engine_transitions")).scalar_one() == 1
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
