"""持久 hold 在真实事务和独立 worker 中保护回滚版本。"""

import copy
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.prompt_bundle import validate_published_bundle
from app.db.models import RuntimeConfigEntry
from app.schemas.response import ApiException
from app.services.prompthub_sync_service import sync_prompthub_bundle
from test.test_prompt_bundle_v2_integrity import canonical_revision, published_v2_fixture
from test.test_prompt_bundle_v2_transactions import add_bundle


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def factory(tmp_path):
    from app.db.models import PromptBundleHoldState, PromptBundleHoldTransition

    engine = create_engine(f"sqlite:///{tmp_path / 'hold.db'}")
    for model in (RuntimeConfigEntry, PromptBundleHoldState, PromptBundleHoldTransition):
        model.__table__.create(engine)
    with (
        patch("app.core.config.settings.PROMPT_P0_BASELINE_ATTESTED", True),
        patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", "apply"),
    ):
        yield sessionmaker(bind=engine)
    engine.dispose()


def versions(factory):
    old = published_v2_fixture()
    new = copy.deepcopy(old)
    new.prompts[5].content += "\n后来发布的版本"
    new.revision = canonical_revision(new)
    add_bundle(factory, validate_published_bundle(old), active=False)
    add_bundle(factory, validate_published_bundle(new), active=True)
    return old, new


def enter(factory, revision):
    from app.services.prompt_bundle_hold_service import enter_prompt_bundle_hold

    return enter_prompt_bundle_hold(revision, actor="admin-1", reason="定位线上回归", session_factory=factory)


def release(factory, revision):
    from app.services.prompt_bundle_hold_service import release_prompt_bundle_hold

    return release_prompt_bundle_hold(revision, actor="admin-2", reason="修复已核验", session_factory=factory)


def active_revision(factory):
    with factory() as session:
        rows = session.query(RuntimeConfigEntry).filter_by(key="fusion:v2", is_active=True).all()
        assert len(rows) == 1
        return rows[0].version


def history(factory):
    from app.db.models import PromptBundleHoldTransition

    with factory() as session:
        return [
            (row.action, row.target_revision, row.actor, row.reason, row.generation)
            for row in session.query(PromptBundleHoldTransition).order_by(PromptBundleHoldTransition.generation)
        ]


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_enter_requires_actual_apply_consumption(factory, mode):
    old, new = versions(factory)
    with patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", mode):
        with pytest.raises(ApiException, match="apply"):
            enter(factory, old.revision)
    assert active_revision(factory) == new.revision
    assert history(factory) == []


@pytest.mark.anyio
async def test_enter_is_atomic_idempotent_and_sync_cannot_override_old_version(factory):
    old, new = versions(factory)
    first = enter(factory, old.revision)
    assert first["state"] == "held"
    assert active_revision(factory) == old.revision
    assert enter(factory, old.revision)["idempotent"] is True
    result = await sync_prompthub_bundle(
        mode="apply",
        client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=new)),
        session_factory=factory,
    )
    assert result["hold_state"] == "held"
    assert result["held_revision"] == old.revision
    assert result["active_revision"] == old.revision
    assert result["revision"] == new.revision
    assert active_revision(factory) == old.revision
    assert history(factory) == [("entered", old.revision, "admin-1", "定位线上回归", 1)]


@pytest.mark.anyio
async def test_release_keeps_history_then_normal_sync_follows_latest(factory):
    old, new = versions(factory)
    enter(factory, old.revision)
    assert release(factory, old.revision)["state"] == "following"
    assert release(factory, old.revision)["idempotent"] is True
    assert active_revision(factory) == old.revision
    result = await sync_prompthub_bundle(
        mode="apply",
        client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=new)),
        session_factory=factory,
    )
    assert result["hold_state"] == "following"
    assert active_revision(factory) == new.revision
    assert history(factory) == [
        ("entered", old.revision, "admin-1", "定位线上回归", 1),
        ("released", old.revision, "admin-2", "修复已核验", 2),
    ]


@pytest.mark.parametrize("operation", ["enter", "release"])
def test_commit_failure_rolls_back_activation_state_and_event(factory, operation):
    from app.services.prompt_bundle_hold_service import get_prompt_bundle_hold

    old, new = versions(factory)
    if operation == "release":
        enter(factory, old.revision)
    before = get_prompt_bundle_hold(session_factory=factory)
    previous_history = history(factory)

    def fail_commit(session):
        raise RuntimeError("事务失败")

    event.listen(factory, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="事务失败"):
            (enter if operation == "enter" else release)(factory, old.revision)
    finally:
        event.remove(factory, "before_commit", fail_commit)
    assert get_prompt_bundle_hold(session_factory=factory) == before
    assert history(factory) == previous_history
    assert active_revision(factory) == (new.revision if operation == "enter" else old.revision)


@pytest.mark.parametrize("failure", ["v1", "corrupt", "catalog", "row_revision", "missing"])
def test_invalid_rollback_target_never_changes_current_state(factory, failure):
    from app.core.prompt_bundle_integrity import compute_local_payload_checksum

    old, new = versions(factory)
    with factory() as session:
        row = session.query(RuntimeConfigEntry).filter_by(version=old.revision).one()
        payload = copy.deepcopy(row.payload)
        if failure == "v1":
            row.key = "fusion"
            payload["schema_version"] = 1
        elif failure == "corrupt":
            payload["prompts"]["limit_summary"]["content"] += "损坏"
        elif failure == "catalog":
            payload["catalog_version"] = "future"
            payload["local_payload_checksum"] = compute_local_payload_checksum(payload)
        elif failure == "row_revision":
            payload = validate_published_bundle(new)
        row.payload = payload
        session.commit()
    with pytest.raises(ApiException):
        enter(factory, "f" * 64 if failure == "missing" else old.revision)
    assert history(factory) == []
    assert active_revision(factory) == new.revision


@pytest.mark.anyio
async def test_remote_failure_does_not_prevent_local_rollback_or_release(factory):
    old, new = versions(factory)
    remote = SimpleNamespace(fetch_published_bundle=AsyncMock(side_effect=RuntimeError("远端 5xx")))
    with patch("app.services.prompthub_sync_service._build_client", side_effect=AssertionError("本地操作禁止远端依赖")):
        enter(factory, old.revision)
        failed = await sync_prompthub_bundle(mode="apply", client=remote, session_factory=factory)
        assert failed["status"] == "error"
        assert active_revision(factory) == old.revision
        release(factory, old.revision)


def test_separate_process_worker_reads_durable_hold_after_restart(factory):
    old, new = versions(factory)
    enter(factory, old.revision)
    script = """
import asyncio, json, sys, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.services.prompthub_sync_service import sync_prompthub_bundle
from test.test_prompt_bundle_v2_integrity import published_v2_fixture, canonical_revision
bundle=published_v2_fixture()
bundle.prompts[5].content += "\\n后来发布的版本"
bundle.revision=canonical_revision(bundle)
from app.core import prompt_bundle as reader
reader._BUNDLE_CACHE=(time.monotonic(), reader.validate_published_bundle(bundle))
settings.PROMPT_P0_BASELINE_ATTESTED=True
factory=sessionmaker(bind=create_engine(sys.argv[1]))
result=asyncio.run(sync_prompthub_bundle(mode="apply", client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=bundle)), session_factory=factory))
print(json.dumps({key: result[key] for key in ("hold_state", "active_revision")}), flush=True)
"""
    for _worker in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script, str(factory.kw["bind"].url)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert f'"active_revision": "{old.revision}"' in result.stdout
        assert '"hold_state": "held"' in result.stdout
    assert active_revision(factory) == old.revision


def test_hold_does_not_deactivate_another_project(factory):
    old, new = versions(factory)
    other = copy.deepcopy(new)
    other.project_slug = "another-project"
    other.revision = canonical_revision(other)
    with patch("app.core.config.settings.PROMPTHUB_PROJECT_SLUG", "another-project"):
        other_id = add_bundle(factory, validate_published_bundle(other), active=True)
    enter(factory, old.revision)
    with factory() as session:
        assert session.get(RuntimeConfigEntry, other_id).is_active


@pytest.mark.anyio
async def test_new_published_version_is_saved_inactive_while_held(factory):
    old, new = versions(factory)
    enter(factory, old.revision)
    new.prompts[5].content += "\n再次发布"
    new.revision = canonical_revision(new)
    result = await sync_prompthub_bundle(
        mode="apply",
        client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=new)),
        session_factory=factory,
    )
    assert result["status"] == "success"
    assert result["hold_state"] == "held"
    with factory() as session:
        candidate = session.query(RuntimeConfigEntry).filter_by(version=new.revision).one()
        assert not candidate.is_active
    assert active_revision(factory) == old.revision


def test_activation_lock_rejects_transaction_snapshots_that_can_precede_hold():
    from unittest.mock import Mock

    from app.services.prompthub_sync_service import _acquire_advisory_lock

    session = Mock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.execute.return_value.scalar_one.return_value = "repeatable read"
    with pytest.raises(ValueError, match="READ COMMITTED"):
        _acquire_advisory_lock(session)


@pytest.mark.anyio
@pytest.mark.parametrize("held", [True, False])
async def test_p0_invalid_candidate_never_writes_or_changes_active(factory, held):
    old, new = versions(factory)
    if held:
        enter(factory, old.revision)
    before = active_revision(factory)
    candidate = copy.deepcopy(new)
    candidate.prompts[0].content += "\n未经原字节门禁的新身份规则"
    candidate.revision = canonical_revision(candidate)
    assert validate_published_bundle(candidate)
    result = await sync_prompthub_bundle(
        mode="apply",
        client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=candidate)),
        session_factory=factory,
    )
    assert result["status"] == "error"
    assert active_revision(factory) == before
    with factory() as session:
        assert session.query(RuntimeConfigEntry).filter_by(version=candidate.revision).count() == 0
