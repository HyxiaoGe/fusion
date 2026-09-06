"""hold 结构、追加式审计和旧 v2 写入者的数据库防线。"""

import copy
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import RuntimeConfigEntry
from test.test_prompt_bundle_hold import enter, versions

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/e4b6c9d2a701_add_prompt_bundle_hold.py"


def load_migration():
    assert PATH.is_file(), "缺少持久 hold 迁移"
    spec = importlib.util.spec_from_file_location("prompt_hold_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrated-hold.db'}")
    RuntimeConfigEntry.__table__.create(engine)
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        load_migration().upgrade()
    with (
        patch("app.core.config.settings.PROMPT_P0_BASELINE_ATTESTED", True),
        patch("app.core.config.settings.PROMPTHUB_SYNC_MODE", "apply"),
    ):
        yield sessionmaker(bind=engine)
    engine.dispose()


def test_upgrade_creates_only_new_state_and_event_tables(migrated_factory):
    inspector = inspect(migrated_factory.kw["bind"])
    assert set(inspector.get_table_names()) == {
        "runtime_config_entries",
        "prompt_bundle_hold_states",
        "prompt_bundle_hold_transitions",
    }
    assert inspector.get_pk_constraint("prompt_bundle_hold_states")["constrained_columns"] == [
        "project_slug",
        "catalog",
    ]
    assert load_migration().down_revision == "c1e5b7a9d204"


@pytest.mark.parametrize(
    "operation",
    ["UPDATE prompt_bundle_hold_transitions SET reason='改写'", "DELETE FROM prompt_bundle_hold_transitions"],
)
def test_transition_events_reject_raw_sql_mutation(migrated_factory, operation):
    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    with pytest.raises(DBAPIError), migrated_factory.begin() as session:
        session.execute(text(operation))


@pytest.mark.parametrize("operation", ["deactivate", "activate_other", "delete", "change_payload"])
def test_older_v2_writer_cannot_override_hold_without_knowing_new_service(migrated_factory, operation):
    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    with pytest.raises(DBAPIError), migrated_factory.begin() as session:
        if operation == "activate_other":
            target = session.query(RuntimeConfigEntry).filter_by(version=new.revision).one()
            target.is_active = True
        else:
            target = session.query(RuntimeConfigEntry).filter_by(version=old.revision).one()
            if operation == "deactivate":
                target.is_active = False
            elif operation == "delete":
                session.delete(target)
            else:
                payload = copy.deepcopy(target.payload)
                payload["prompts"]["limit_summary"]["content"] += "篡改"
                target.payload = payload
        session.flush()


def test_following_state_still_allows_normal_switch_and_retains_audit(migrated_factory):
    from test.test_prompt_bundle_hold import active_revision, history, release

    old, new = versions(migrated_factory)
    enter(migrated_factory, old.revision)
    release(migrated_factory, old.revision)
    with migrated_factory.begin() as session:
        for row in session.query(RuntimeConfigEntry):
            row.is_active = row.version == new.revision
    assert active_revision(migrated_factory) == new.revision
    assert len(history(migrated_factory)) == 2


def test_schema_downgrade_does_not_delete_persistent_governance_data():
    with pytest.raises(RuntimeError, match="保留"):
        load_migration().downgrade()


def test_postgresql_guard_uses_same_transaction_lock_and_covers_all_mutations():
    from app.services.prompthub_sync_service import _ADVISORY_LOCK_ID

    migration = load_migration()
    with patch.object(migration, "op") as operation:
        operation.get_bind.return_value.dialect.name = "postgresql"
        migration.upgrade()
    sql = "\n".join(str(call.args[0]) for call in operation.execute.call_args_list)
    assert f"pg_advisory_xact_lock({_ADVISORY_LOCK_ID})" in sql
    assert "plpgsql VOLATILE" in sql
    assert "current_setting('transaction_isolation') <> 'read committed'" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON runtime_config_entries" in sql
    assert "BEFORE UPDATE OR DELETE ON prompt_bundle_hold_transitions" in sql


@pytest.mark.parametrize("existing_candidate", [True, False])
def test_real_p3a_sync_writer_is_blocked_while_held_then_follows_after_release(migrated_factory, existing_candidate):
    import asyncio
    import hashlib
    import types
    from unittest.mock import AsyncMock

    from test.test_prompt_bundle_hold import active_revision, history, release
    from test.test_prompt_bundle_v2_integrity import canonical_revision

    path = Path(__file__).parent / "fixtures/prompt_bundle/p3a_sync.py"
    source = path.read_bytes()
    expected = "1fc5150883d4a5f85f022c9278bb779c9a2d37df1567047728c3f6f8e240fb3f"
    assert hashlib.sha256(source).hexdigest() == expected
    previous = types.ModuleType("app.services.p3a_compatibility_probe")
    exec(compile(source, str(path), "exec"), previous.__dict__)
    old, new = versions(migrated_factory)
    if not existing_candidate:
        new.prompts[5].content += "\n远端新 revision"
        new.revision = canonical_revision(new)
    client = types.SimpleNamespace(fetch_published_bundle=AsyncMock(return_value=new))
    enter(migrated_factory, old.revision)
    blocked = asyncio.run(previous.sync_prompthub_bundle(mode="apply", client=client, session_factory=migrated_factory))
    assert blocked["status"] != "success"
    assert active_revision(migrated_factory) == old.revision
    release(migrated_factory, old.revision)
    following = asyncio.run(
        previous.sync_prompthub_bundle(mode="apply", client=client, session_factory=migrated_factory)
    )
    assert following["status"] == "success"
    assert active_revision(migrated_factory) == new.revision
    assert len(history(migrated_factory)) == 2
