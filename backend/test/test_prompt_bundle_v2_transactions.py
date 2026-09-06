"""在真实 SQLAlchemy 事务中验证隔离、切换和失败恢复。"""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.prompt_bundle import clear_prompt_bundle_cache, validate_published_bundle
from app.db.models import RuntimeConfigEntry
from app.services.prompthub_sync_service import sync_prompthub_bundle
from test.test_prompt_bundle_v2_integrity import canonical_revision, published_v2_fixture


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompt.db'}")
    RuntimeConfigEntry.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    clear_prompt_bundle_cache()
    yield factory
    clear_prompt_bundle_cache()
    engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def add_bundle(factory, payload, *, key="fusion:v2", active=True):
    with factory() as session:
        row = RuntimeConfigEntry(
            namespace="prompt_bundle",
            key=key,
            version=payload["revision"],
            payload=copy.deepcopy(payload),
            is_active=active,
        )
        session.add(row)
        session.commit()
        return row.id


def load_rows(factory):
    with factory() as session:
        return {row.id: row for row in session.query(RuntimeConfigEntry).all()}


async def synchronize(factory, bundle, mode="apply"):
    with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
        return await sync_prompthub_bundle(
            mode=mode,
            client=SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=bundle)),
            session_factory=factory,
        )


@pytest.mark.anyio
async def test_v2_activation_keeps_same_revision_v1_untouched(session_factory):
    bundle = published_v2_fixture()
    legacy = {**validate_published_bundle(bundle), "schema_version": 1}
    old_id = add_bundle(session_factory, legacy, key="fusion")
    result = await synchronize(session_factory, bundle)
    rows = load_rows(session_factory)
    assert result["status"] == "success"
    assert rows[old_id].payload == legacy
    assert rows[old_id].is_active
    current = [row for row in rows.values() if row.key == "fusion:v2"]
    assert len(current) == 1
    assert current[0].version == rows[old_id].version
    assert current[0].is_active


@pytest.mark.anyio
async def test_shadow_never_deactivates_existing_active_v2(session_factory):
    bundle = published_v2_fixture()
    row_id = add_bundle(session_factory, validate_published_bundle(bundle))
    result = await synchronize(session_factory, bundle, "shadow")
    assert result["status"] == "success"
    assert result["idempotent"] is True
    assert result["active"] is True
    assert load_rows(session_factory)[row_id].is_active


@pytest.mark.anyio
async def test_same_source_revision_conflict_is_distinct_and_does_not_repair_row(session_factory):
    bundle = published_v2_fixture()
    corrupted = validate_published_bundle(bundle)
    corrupted["prompts"]["limit_summary"]["content"] += "损坏"
    row_id = add_bundle(session_factory, corrupted)
    result = await synchronize(session_factory, bundle)
    assert result["status"] == "revision_conflict"
    persisted = load_rows(session_factory)[row_id]
    assert persisted.payload == corrupted
    assert persisted.is_active


@pytest.mark.anyio
async def test_diff_uses_active_v2_and_keeps_code_default_diff_separate(session_factory):
    bundle = published_v2_fixture()
    bundle.prompts[5].content += "\n当前实际线上正文"
    bundle.revision = canonical_revision(bundle)
    add_bundle(session_factory, validate_published_bundle(bundle))
    result = await synchronize(session_factory, bundle, "shadow")
    assert result["changed_prompt_keys"] == []
    assert result["code_default_changed_prompt_keys"] == ["limit_summary"]


@pytest.mark.anyio
async def test_commit_failure_preserves_old_active_v2_and_v1(session_factory):
    bundle = published_v2_fixture()
    before = validate_published_bundle(bundle)
    v1_id = add_bundle(session_factory, {**before, "schema_version": 1}, key="fusion")
    v2_id = add_bundle(session_factory, before)
    bundle.prompts[5].content += "\n新版本"
    bundle.revision = canonical_revision(bundle)

    def fail_commit(session):
        raise RuntimeError("注入事务提交失败")

    event.listen(session_factory, "before_commit", fail_commit)
    try:
        result = await synchronize(session_factory, bundle)
    finally:
        event.remove(session_factory, "before_commit", fail_commit)
    assert result["status"] == "error"
    after = load_rows(session_factory)
    assert set(after) == {v1_id, v2_id}
    assert all(row.is_active for row in after.values())
    assert after[v2_id].payload == before
