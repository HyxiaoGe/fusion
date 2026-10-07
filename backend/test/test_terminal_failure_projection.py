"""会话只读响应恢复无正文的失败结果，模型历史与物理消息不受影响。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import AgentProgressSnapshot, AgentSession, Conversation, Message, Notification, User
from app.db.repositories import ConversationRepository
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService
from app.services.notification_service import NotificationService, enqueue_run_notification

NOW = datetime(2026, 10, 7, 10, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        session.add(User(id="user", username="user"))
        session.flush()
        session.add(Conversation(id="conv", user_id="user", title="会话", model_id="test"))
        session.flush()
        session.add_all(
            [
                Message(
                    id="turn-1",
                    conversation_id="conv",
                    sequence=10,
                    role="user",
                    content=[{"type": "text", "text": "问题一"}],
                    created_at=NOW,
                ),
                Message(
                    id="turn-2",
                    conversation_id="conv",
                    sequence=20,
                    role="user",
                    content=[{"type": "text", "text": "问题二"}],
                    created_at=NOW + timedelta(minutes=2),
                ),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def add_run(
    db,
    run_id,
    *,
    turn_id="turn-1",
    message_id="missing-answer",
    status="error",
    attempt=0,
    notify_interrupted=False,
    terminal=True,
    created_at=NOW,
):
    run = AgentSession(
        id=run_id,
        conversation_id="conv",
        user_id="user",
        turn_message_id=turn_id,
        message_id=message_id,
        model_id="test",
        provider="test",
        status=status,
        attempt_index=attempt,
        terminal_at=NOW + timedelta(seconds=30) if terminal and status != "running" else None,
        created_at=created_at,
        error_message="PRIVATE 原始失败信息",
        run_config={"max_steps": 3, "system_prompt_snapshot": {"content": "PRIVATE 提示词"}},
    )
    db.add(run)
    db.flush()
    enqueue_run_notification(db, run, notify_interrupted=notify_interrupted)
    db.commit()
    return run


def display_conversation(db):
    service = ChatService.__new__(ChatService)
    service.conversation_service = ConversationService(db)
    return service.get_conversation("conv", "user")


def test_failed_preparation_is_displayed_after_its_turn_without_any_get_write(db):
    run = add_run(db, "failed")
    db.add(
        AgentProgressSnapshot(
            run_id=run.id, conversation_id="conv", user_id="user", state={"plan": {"plan_id": "plan"}}
        )
    )
    db.commit()
    statements = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        display = display_conversation(db)
        history = ConversationService(db).get_conversation("conv", "user")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture_statement)

    assert [message.id for message in display.messages] == ["turn-1", "missing-answer", "turn-2"]
    projected = display.messages[1]
    assert projected.persisted is False
    assert projected.sequence == 11
    assert projected.role == "assistant"
    assert projected.content == []
    assert projected.agent_run.run_id == "failed"
    assert projected.agent_run.status == "error"
    assert projected.agent_run.progress["plan"]["plan_id"] == "plan"
    assert projected.agent_run.config == {"max_steps": 3}
    assert "PRIVATE" not in display.model_dump_json()
    assert [message.id for message in history.messages] == ["turn-1", "turn-2"]
    assert all(message.persisted for message in history.messages)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert db.scalar(select(func.count()).select_from(Message)) == 2
    assert not db.new and not db.dirty
    assert (
        NotificationService(db)
        .mark_result_read(
            "user", conversation_id="conv", results=[{"run_id": "failed", "message_id": "missing-answer"}]
        )
        .updated_count
        == 1
    )


def test_latest_attempt_hides_old_failure_while_new_run_is_running(db):
    add_run(db, "old-failure", attempt=0, created_at=NOW + timedelta(hours=1))
    add_run(db, "new-running", attempt=1, message_id="new-answer", status="running", created_at=NOW)
    display = display_conversation(db)
    assert [message.id for message in display.messages] == ["turn-1", "turn-2"]


def test_newest_failure_is_projected_once_with_its_exact_run(db):
    add_run(db, "old-failure", attempt=0)
    add_run(db, "new-failure", attempt=1, message_id="new-answer")
    display = display_conversation(db)
    assert [message.id for message in display.messages] == ["turn-1", "new-answer", "turn-2"]
    assert display.messages[1].agent_run.run_id == "new-failure"


@pytest.mark.parametrize("message_id", ["actual-answer", "different-missing-answer"])
def test_failure_does_not_duplicate_or_replace_existing_answer(db, message_id):
    db.add(
        Message(
            id="actual-answer",
            conversation_id="conv",
            sequence=11,
            role="assistant",
            content=[{"type": "text", "text": "已保存的回答"}],
            created_at=NOW + timedelta(seconds=1),
        )
    )
    db.commit()
    add_run(db, "latest-error", message_id=message_id)
    display = display_conversation(db)
    assert [message.id for message in display.messages] == ["turn-1", "actual-answer", "turn-2"]
    assert display.messages[1].persisted is True
    assert display.messages[1].content[0].text == "已保存的回答"
    if message_id == "actual-answer":
        assert display.messages[1].agent_run.run_id == "latest-error"


def test_orphan_interrupted_is_displayed_but_active_user_stop_is_quiet(db):
    add_run(db, "orphan", status="interrupted", notify_interrupted=True)
    add_run(db, "user-stop", status="interrupted", turn_id="turn-2", message_id="stopped-answer")
    display = display_conversation(db)
    assert [message.id for message in display.messages] == ["turn-1", "missing-answer", "turn-2"]
    assert display.messages[1].agent_run.status == "interrupted"
    assert db.scalar(select(func.count()).select_from(Notification)) == 1


@pytest.mark.parametrize(
    "status,terminal", [("error", False), ("running", True), ("completed", True), ("limit_reached", True)]
)
def test_only_saved_failure_terminals_are_projected(db, status, terminal):
    add_run(db, "run", status=status, terminal=terminal)
    assert [message.id for message in display_conversation(db).messages] == ["turn-1", "turn-2"]


def test_unanchored_or_foreign_run_cannot_create_display_row(db):
    run = add_run(db, "orphan-turn")
    run.turn_message_id = "missing-user"
    db.commit()
    assert [message.id for message in display_conversation(db).messages] == ["turn-1", "turn-2"]
    run.turn_message_id = "turn-1"
    run.user_id = "other"
    db.commit()
    assert [message.id for message in display_conversation(db).messages] == ["turn-1", "turn-2"]
    assert ConversationRepository(db).get_by_id("conv", "other", project_terminal_failures=True) is None
