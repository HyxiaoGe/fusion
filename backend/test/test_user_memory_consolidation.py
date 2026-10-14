"""后台记忆整理：挑选闲置对话、渲染输入、解析与应用操作、进度推进。"""

import asyncio
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.database import Base
from app.db.models import Conversation, MemoryConsolidationCursor, Message, User, UserMemory
from app.services import user_memory_consolidation as consolidation
from app.utils.time import utc_now

NOW = utc_now()


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            Message.__table__,
            UserMemory.__table__,
            MemoryConsolidationCursor.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                User(id="user-1", username="one"),
                User(id="guest", username="guest", is_guest=True),
                User(id="off", username="off", memory_enabled=False),
            ]
        )
        db.commit()
    yield factory
    engine.dispose()


_sequence = iter(range(1, 10_000))


def add_conversation(factory, conversation_id, *, user_id="user-1", turns=(("user", "你好"),), minutes_ago=60):
    with factory() as db:
        db.add(Conversation(id=conversation_id, user_id=user_id, title="t", model_id="m"))
        db.flush()
        for offset, (role, text) in enumerate(turns):
            db.add(
                Message(
                    id=f"{conversation_id}-{offset}",
                    conversation_id=conversation_id,
                    role=role,
                    content=[{"type": "text", "id": "b", "text": text}],
                    sequence=next(_sequence),
                    created_at=NOW - timedelta(minutes=minutes_ago) + timedelta(seconds=offset),
                )
            )
        db.commit()


def pending_ids(factory):
    with factory() as db:
        return [item.conversation_id for item in consolidation.find_pending_conversations(db, now=NOW)]


def test_pending_requires_idle_recent_registered_enabled_and_unprocessed(session_factory):
    add_conversation(session_factory, "idle")
    add_conversation(session_factory, "active", minutes_ago=5)
    add_conversation(session_factory, "stale", minutes_ago=60 * 24 * 4)
    add_conversation(session_factory, "guest", user_id="guest")
    add_conversation(session_factory, "off", user_id="off")
    add_conversation(session_factory, "done")
    with session_factory() as db:
        last = db.query(Message).filter(Message.conversation_id == "done").one().sequence
        db.add(MemoryConsolidationCursor(conversation_id="done", processed_sequence=last))
        db.commit()

    assert pending_ids(session_factory) == ["idle"]


def test_render_conversation_keeps_text_and_truncates_answers():
    messages = [
        Message(role="user", content=[{"type": "text", "text": "我住在杭州西湖区"}]),
        Message(
            role="assistant", content=[{"type": "thinking", "thinking": "略"}, {"type": "text", "text": "好" * 3000}]
        ),
        Message(role="assistant", content=[{"type": "search", "query": "x"}]),
    ]
    rendered = consolidation.render_conversation(messages)

    assert rendered.startswith("User: 我住在杭州西湖区\n\nAssistant: ")
    assert rendered.count("好") == 1000
    assert "略" not in rendered


def test_parse_operations_accepts_fenced_json_and_rejects_garbage():
    raw = '```json\n{"operations": [{"op": "add", "category": "health", "content": "糖尿病"}, "bad"]}\n```'
    assert consolidation.parse_operations(raw) == [{"op": "add", "category": "health", "content": "糖尿病"}]
    with pytest.raises(consolidation.MemoryConsolidationOutputError):
        consolidation.parse_operations("我觉得没什么要记的")
    with pytest.raises(consolidation.MemoryConsolidationOutputError):
        consolidation.parse_operations('{"ops": []}')


def test_apply_operations_checks_ownership_and_format(session_factory):
    add_conversation(session_factory, "conv")
    with session_factory() as db:
        db.add_all(
            [
                UserMemory(id="mine", user_id="user-1", category="residence", content="上海"),
                UserMemory(id="gone", user_id="user-1", category="interests", content="围棋"),
                UserMemory(id="theirs", user_id="off", category="name", content="别人"),
            ]
        )
        db.commit()
        applied = consolidation.apply_operations(
            db,
            user_id="user-1",
            conversation_id="conv",
            operations=[
                {"op": "update", "id": "mine", "content": "杭州西湖区文三路"},
                {"op": "delete", "id": "gone"},
                {"op": "delete", "id": "theirs"},
                {"op": "update", "id": "theirs", "content": "改别人的"},
                {"op": "add", "category": "Health Info", "content": "糖尿病"},
                {"op": "add", "category": "health", "content": "长" * 201},
                {"op": "add", "category": "health", "content": " 2 型糖尿病，\n在控糖 "},
            ],
        )
        db.commit()
        rows = {row.id: row for row in db.query(UserMemory).all()}

    assert applied == {"add": 1, "update": 1, "delete": 1, "skipped": 4}
    assert rows["mine"].content == "杭州西湖区文三路"
    assert rows["mine"].category == "residence"
    assert rows["mine"].source_conversation_id == "conv"
    assert "gone" not in rows
    assert rows["theirs"].content == "别人"
    added = [row for row in rows.values() if row.category == "health"]
    assert [row.content for row in added] == ["2 型糖尿病， 在控糖"]


def test_apply_operations_caps_total_items(session_factory):
    add_conversation(session_factory, "conv")
    with session_factory() as db:
        db.add_all(
            UserMemory(user_id="user-1", category="other", content=f"条目{i}")
            for i in range(consolidation.MAX_MEMORY_ITEMS)
        )
        db.commit()
        applied = consolidation.apply_operations(
            db,
            user_id="user-1",
            conversation_id="conv",
            operations=[{"op": "add", "category": "other", "content": "再多一条"}],
        )
    assert applied["skipped"] == 1


def run(coro):
    return asyncio.run(coro)


def test_consolidation_applies_model_output_and_advances_cursor(session_factory):
    add_conversation(session_factory, "conv", turns=(("user", "我有糖尿病"), ("assistant", "好的，饮食上注意控糖")))
    prompts = []

    async def call_model(prompt, metadata):
        prompts.append(prompt)
        return json.dumps({"operations": [{"op": "add", "category": "health", "content": "糖尿病"}]})

    assert run(consolidation.consolidate_user_memories(session_factory=session_factory, call_model=call_model)) == 1

    assert "User: 我有糖尿病" in prompts[0]
    assert "(none)" in prompts[0]
    with session_factory() as db:
        assert [row.content for row in db.query(UserMemory).all()] == ["糖尿病"]
    assert pending_ids(session_factory) == []

    # 同一对话再来新消息时只处理新增部分，并带上已有记忆。
    with session_factory() as db:
        db.add(
            Message(
                id="conv-new",
                conversation_id="conv",
                role="user",
                content=[{"type": "text", "text": "其实是 2 型"}],
                sequence=next(_sequence),
                created_at=NOW - timedelta(minutes=40),
            )
        )
        db.commit()
    run(consolidation.consolidate_user_memories(session_factory=session_factory, call_model=call_model))
    assert "我有糖尿病" not in prompts[1]
    assert "User: 其实是 2 型" in prompts[1]
    assert "| health | 糖尿病" in prompts[1]


def test_failures_retry_then_skip_batch(session_factory):
    add_conversation(session_factory, "conv")
    calls = []

    async def broken(prompt, metadata):
        calls.append(prompt)
        return "not json"

    for attempt in range(consolidation.MAX_FAILED_ATTEMPTS):
        assert pending_ids(session_factory) == ["conv"], attempt
        run(consolidation.consolidate_user_memories(session_factory=session_factory, call_model=broken))

    assert len(calls) == consolidation.MAX_FAILED_ATTEMPTS
    assert pending_ids(session_factory) == []
    with session_factory() as db:
        assert db.get(MemoryConsolidationCursor, "conv").failed_attempts == 0


def test_memory_disabled_during_consolidation_writes_nothing(session_factory):
    add_conversation(session_factory, "conv")

    async def call_model(prompt, metadata):
        with session_factory() as db:
            db.get(User, "user-1").memory_enabled = False
            db.commit()
        return json.dumps({"operations": [{"op": "add", "category": "name", "content": "小明"}]})

    run(consolidation.consolidate_user_memories(session_factory=session_factory, call_model=call_model))

    with session_factory() as db:
        assert db.query(UserMemory).count() == 0


def test_run_budget_stops_starting_new_conversations(session_factory):
    add_conversation(session_factory, "a")
    add_conversation(session_factory, "b")

    async def call_model(prompt, metadata):
        return '{"operations": []}'

    processed = run(
        consolidation.consolidate_user_memories(
            session_factory=session_factory, call_model=call_model, budget_seconds=0
        )
    )
    assert processed == 0


def test_migration_round_trip():
    path = Path(__file__).parents[1] / "alembic/versions/d8f2b6c4e135_add_memory_consolidation_cursors.py"
    spec = importlib.util.spec_from_file_location("memory_cursor_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE conversations (id VARCHAR PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            references = inspect(connection).get_foreign_keys("memory_consolidation_cursors")
            assert references[0]["options"]["ondelete"] == "CASCADE"
            migration.downgrade()
            assert "memory_consolidation_cursors" not in inspect(connection).get_table_names()


def test_job_skips_when_lock_is_held(monkeypatch):
    class HeldRedis:
        def set(self, *args, **kwargs):
            return False

    called = []
    monkeypatch.setattr(consolidation, "get_redis", lambda: HeldRedis())

    async def fake_consolidate(**kwargs):
        called.append(True)

    monkeypatch.setattr(consolidation, "consolidate_user_memories", fake_consolidate)
    run(consolidation.consolidate_user_memories_job())
    assert called == []
