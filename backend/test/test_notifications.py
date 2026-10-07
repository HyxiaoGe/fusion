"""站内通知的事务、修订水位、精确结果与鉴权协议回归。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.database import Base
from app.db.models import AgentSession, Changelog, Conversation, Notification, NotificationUserState, User
from app.db.notification_repository import NotificationRepository
from app.db.repositories import ConversationRepository
from app.schemas.response import ApiException
from app.services.notification_service import NotificationService, enqueue_run_notification, get_unread_conversation_ids
from app.utils.time import utc_now


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            AgentSession.__table__,
            Changelog.__table__,
            NotificationUserState.__table__,
            Notification.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all([User(id="user-1", username="one"), User(id="user-2", username="two")])
        session.flush()
        session.add_all(
            [
                Conversation(id=cid, user_id=uid, title=cid, model_id="test")
                for cid, uid in [("conv-1", "user-1"), ("conv-2", "user-1"), ("conv-other", "user-2")]
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def add_run(
    db, run_id, *, user_id="user-1", conversation_id="conv-1", message_id=None, status="completed", notify=True
):
    run = AgentSession(
        id=run_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id or f"msg-{run_id}",
        model_id="test",
        provider="test",
        status=status,
        terminal_at=utc_now() if status != "running" else None,
        error_message="敏感原始错误和工具输出",
    )
    db.add(run)
    db.flush()
    notification = enqueue_run_notification(db, run) if notify else None
    db.commit()
    return run, notification


def test_terminal_notification_and_revision_rollback_together(db):
    run, _ = add_run(db, "atomic", status="running")
    run.status = "completed"
    run.terminal_at = utc_now()
    notification = enqueue_run_notification(db, run)
    assert notification.created_revision == 1
    db.rollback()

    assert db.get(AgentSession, run.id).status == "running"
    assert db.scalar(select(Notification.id)) is None
    assert db.get(NotificationUserState, "user-1") is None


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("completed", "run_completed"),
        ("error", "run_failed"),
        ("limit_reached", "run_limit_reached"),
        ("incomplete", "run_incomplete"),
    ],
)
def test_terminal_kinds_are_idempotent_and_do_not_expose_raw_errors(db, status, kind):
    run, notification = add_run(db, "terminal", status=status)
    duplicate = enqueue_run_notification(db, run)
    db.commit()
    assert duplicate.id == notification.id
    assert duplicate.kind == kind
    assert duplicate.title.startswith("conv-1")
    assert "敏感" not in duplicate.body
    assert db.get(NotificationUserState, "user-1").revision == 1
    assert NotificationRepository(db).unread_count("user-1") == 1


def test_user_stop_is_quiet_but_orphan_interrupt_can_notify(db):
    run, notification = add_run(db, "stopped", status="interrupted")
    assert notification is None
    assert db.get(NotificationUserState, "user-1") is None
    notification = enqueue_run_notification(db, run, notify_interrupted=True)
    db.commit()
    assert notification.kind == "run_interrupted"


def test_enqueue_rejects_inconsistent_source_ownership(db):
    run, _ = add_run(db, "wrong-owner", user_id="user-2", notify=False)
    with pytest.raises(ValueError, match="归属"):
        enqueue_run_notification(db, run)
    assert db.scalar(select(Notification.id)) is None


def test_notification_title_captures_source_and_keeps_terminal_suffix(db):
    conversation = db.get(Conversation, "conv-1")
    conversation.title = "攻略" * 100
    db.commit()
    _, notification = add_run(db, "title")
    assert len(notification.title) == 120
    assert notification.title.endswith("已完成")
    saved_title = notification.title
    conversation.title = "重命名"
    db.commit()
    assert NotificationService(db).list_notifications("user-1").items[0].title == saved_title


def test_read_all_uses_observed_revision_and_preserves_new_notifications(db):
    _, first = add_run(db, "first")
    service = NotificationService(db)
    observed = service.list_notifications("user-1")
    _, newer = add_run(db, "newer")

    result = service.mark_all_read("user-1", observed.revision)
    assert result.updated_count == 1
    assert result.unread_count == 1
    assert result.revision == 3
    page = service.list_notifications("user-1", filter="unread")
    assert [row.id for row in page.items] == [newer.id]
    assert service.mark_all_read("user-1", observed.revision).revision == 3
    assert service.mark_read("user-1", [first.id, first.id]).updated_count == 0


def test_pagination_and_global_unread_projection_survive_read_revision_gaps(db):
    _, oldest = add_run(db, "oldest", conversation_id="conv-2")
    service = NotificationService(db)
    service.mark_read("user-1", [oldest.id])
    _, middle = add_run(db, "middle", conversation_id="conv-2")
    _, latest = add_run(db, "latest")
    first = service.list_notifications("user-1", limit=1)
    assert first.revision == 4
    assert first.items[0].id == latest.id
    assert first.next_cursor == "4"
    assert first.unread_count == 2
    assert first.unread_conversation_ids == ["conv-1", "conv-2"]
    second = service.list_notifications("user-1", cursor=first.next_cursor, limit=1)
    assert second.items[0].id == middle.id
    assert second.next_cursor == "3"
    third = service.list_notifications("user-1", cursor=second.next_cursor, limit=1)
    assert third.items[0].id == oldest.id
    assert third.items[0].read_at is not None
    assert third.next_cursor is None
    assert third.items[0].created_at.utcoffset().total_seconds() == 0
    assert get_unread_conversation_ids(db, "user-1") == ["conv-1", "conv-2"]


def test_exact_run_bulk_read_does_not_clear_retry_or_other_message(db):
    first, _ = add_run(db, "attempt-1", message_id="same-message")
    retry, _ = add_run(db, "attempt-2", message_id="same-message")
    other, _ = add_run(db, "other-message")
    service = NotificationService(db)
    result = service.mark_result_read(
        "user-1",
        conversation_id="conv-1",
        results=[
            {"run_id": first.id, "message_id": first.message_id},
            {"run_id": other.id, "message_id": other.message_id},
        ],
    )
    assert result.updated_count == 2
    assert result.unread_count == 1
    assert [row.target.run_id for row in service.list_notifications("user-1", filter="unread").items] == [retry.id]
    duplicate = service.mark_result_read(
        "user-1",
        conversation_id="conv-1",
        results=[{"run_id": first.id, "message_id": first.message_id}] * 2,
    )
    assert duplicate.updated_count == 0
    assert duplicate.revision == result.revision


@pytest.mark.parametrize("wrong", ["conversation", "message", "user", "pending", "missing-terminal"])
def test_result_ack_validates_entire_batch_before_any_read(db, wrong):
    first, _ = add_run(db, "valid")
    second, _ = add_run(db, "second", status="running" if wrong == "pending" else "completed")
    if wrong == "missing-terminal":
        second.terminal_at = None
        db.commit()
    results = [
        {"run_id": first.id, "message_id": first.message_id},
        {"run_id": second.id, "message_id": "wrong" if wrong == "message" else second.message_id},
    ]
    with pytest.raises(ApiException) as error:
        NotificationService(db).mark_result_read(
            "user-2" if wrong == "user" else "user-1",
            conversation_id="conv-2" if wrong == "conversation" else "conv-1",
            results=results,
        )
    assert error.value.status_code == (409 if wrong in ("pending", "missing-terminal") else 404)
    assert NotificationRepository(db).unread_count("user-1") == (1 if wrong == "pending" else 2)


def test_users_are_isolated_for_reads_and_revision(db):
    _, own = add_run(db, "own")
    _, foreign = add_run(db, "foreign", user_id="user-2", conversation_id="conv-other")
    service = NotificationService(db)
    assert [row.id for row in service.list_notifications("user-1").items] == [own.id]
    assert service.mark_read("user-1", [foreign.id]).updated_count == 0
    assert service.mark_all_read("user-1", 1).updated_count == 1
    assert service.list_notifications("user-2").unread_count == 1
    assert service.list_notifications("user-2").revision == 1


def test_deleted_source_removes_notifications_and_unread_projection(db):
    run, notification = add_run(db, "deleted")
    reference = {"run_id": run.id, "message_id": run.message_id}
    deleted_notification_id = notification.id
    _, already_read = add_run(db, "deleted-read")
    _, retained = add_run(db, "retained", conversation_id="conv-2")
    service = NotificationService(db)
    service.mark_read("user-1", [already_read.id, retained.id])
    previous_revision = service.list_notifications("user-1").revision
    assert ConversationRepository(db).delete("conv-1", "user-1")
    page = service.list_notifications("user-1")
    assert [row.id for row in page.items] == [retained.id]
    assert page.items[0].read_at is not None
    assert page.unread_count == 0
    assert page.unread_conversation_ids == []
    assert page.revision == previous_revision + 1
    assert get_unread_conversation_ids(db, "user-1") == []
    with pytest.raises(ApiException) as error:
        service.mark_result_read("user-1", conversation_id="conv-1", results=[reference])
    assert error.value.status_code == 404
    assert service.mark_read("user-1", [deleted_notification_id]).updated_count == 0


def test_delete_without_notifications_does_not_create_or_advance_revision(db):
    assert ConversationRepository(db).delete("conv-1", "user-1")
    assert db.get(NotificationUserState, "user-1") is None
    add_run(db, "retained", conversation_id="conv-2")
    assert ConversationRepository(db).delete("conv-other", "user-1") is False
    assert db.get(NotificationUserState, "user-1").revision == 1


@pytest.mark.parametrize("cursor", ["", "0", "-1", "abc", "１２", str(2**63)])
def test_invalid_pagination_cursors_are_rejected(db, cursor):
    with pytest.raises(ApiException) as error:
        NotificationService(db).list_notifications("user-1", cursor=cursor)
    assert error.value.status_code == 400


def test_read_all_rejects_unobserved_future_revision(db):
    add_run(db, "own")
    with pytest.raises(ApiException) as error:
        NotificationService(db).mark_all_read("user-1", 2)
    assert error.value.status_code == 400
    assert NotificationRepository(db).unread_count("user-1") == 1


def test_postgresql_read_and_write_use_user_row_locks():
    db = MagicMock()
    repository = NotificationRepository(db)
    repository.lock_state("user-1", shared=True)
    statement = db.execute.call_args.args[0]
    assert str(statement.compile(dialect=postgresql.dialect())).endswith("FOR SHARE")
    repository.lock_state("user-1")
    statement = db.execute.call_args.args[0]
    assert str(statement.compile(dialect=postgresql.dialect())).endswith("FOR UPDATE")


def test_first_user_state_creation_uses_conflict_safe_insert_before_lock():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    NotificationRepository(db).lock_state("user-1", create=True)
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert "ON CONFLICT (user_id) DO NOTHING" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert str(statements[1].compile(dialect=postgresql.dialect())).endswith("FOR UPDATE")


@pytest.mark.parametrize("duplicate", ["source", "revision"])
def test_database_uniqueness_defends_idempotency_and_revision(db, duplicate):
    _, notification = add_run(db, "original")
    db.add(
        Notification(
            user_id="user-1",
            run_id=notification.run_id if duplicate == "source" else "different-run",
            kind=notification.kind,
            conversation_id="conv-1",
            message_id="m",
            title="t",
            body="b",
            created_revision=notification.created_revision if duplicate == "revision" else 2,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert NotificationRepository(db).unread_count("user-1") == 1


@pytest.fixture
def client(db):
    from app.api.deps import get_current_user, get_db
    from app.api.notifications import router

    app = FastAPI()
    app.include_router(router, prefix="/api/notifications")

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    @app.exception_handler(ApiException)
    async def handle_api_error(request: Request, error: ApiException):
        return JSONResponse(status_code=error.status_code, content={"code": error.code, "message": error.message})

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")
    with TestClient(app) as test_client:
        yield test_client


def test_api_contract_and_bulk_result_read(client, db):
    run, _ = add_run(db, "api")
    response = client.get("/api/notifications?limit=1")
    assert response.status_code == 200
    assert response.json()["request_id"] == "test-request"
    data = response.json()["data"]
    assert data["unread_conversation_ids"] == ["conv-1"]
    assert data["revision"] == 1
    assert data["items"][0]["target"] == {
        "type": "conversation",
        "conversation_id": "conv-1",
        "run_id": run.id,
        "message_id": run.message_id,
    }
    assert data["items"][0]["created_at"].endswith("Z")
    response = client.post(
        "/api/notifications/read-result",
        json={"conversation_id": "conv-1", "results": [{"run_id": run.id, "message_id": run.message_id}]},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "updated_count": 1,
        "unread_count": 0,
        "unread_conversation_ids": [],
        "revision": 2,
    }


def test_api_requires_login(client):
    from app.api.deps import get_current_user

    del client.app.dependency_overrides[get_current_user]
    assert client.get("/api/notifications").status_code == 401


@pytest.mark.parametrize(
    "path,payload",
    [
        ("read", {"ids": []}),
        ("read", {"ids": ["n"] * 101}),
        ("read-all", {"through_revision": "1"}),
        ("read-all", {"through_revision": -1}),
        ("read-result", {"conversation_id": "c", "results": []}),
        ("read-result", {"conversation_id": "c", "run_id": "r", "message_id": "m"}),
    ],
)
def test_api_rejects_invalid_mutation_shapes(client, path, payload):
    assert client.post(f"/api/notifications/{path}", json=payload).status_code == 422


def test_api_rejects_foreign_or_pending_results(client, db):
    foreign, _ = add_run(db, "foreign", user_id="user-2", conversation_id="conv-other")
    pending, _ = add_run(db, "pending", status="running")
    for run, expected in [(foreign, 404), (pending, 409)]:
        response = client.post(
            "/api/notifications/read-result",
            json={
                "conversation_id": run.conversation_id,
                "results": [{"run_id": run.id, "message_id": run.message_id}],
            },
        )
        assert response.status_code == expected


def test_notification_migration_round_trip_and_constraints():
    path = Path(__file__).parents[1] / "alembic/versions/6a1e9f3c8b20_add_notifications.py"
    spec = importlib.util.spec_from_file_location("notification_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE conversations (id VARCHAR PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            schema = inspect(connection)
            assert {tuple(row["column_names"]) for row in schema.get_unique_constraints("notifications")} == {
                ("user_id", "run_id", "kind"),
                ("user_id", "created_revision"),
            }
            indexes = {row["name"]: row for row in schema.get_indexes("notifications")}
            assert indexes["ix_notifications_conversation_id"]["column_names"] == ["conversation_id"]
            index = indexes["ix_notifications_user_unread_revision"]
            assert str(index["dialect_options"]["sqlite_where"]) == "read_at IS NULL"
            references = {row["referred_table"]: row for row in schema.get_foreign_keys("notifications")}
            assert references["conversations"]["options"]["ondelete"] == "CASCADE"
            assert references["users"]["options"]["ondelete"] == "CASCADE"
            connection.exec_driver_sql("INSERT INTO users (id) VALUES ('user')")
            connection.exec_driver_sql("INSERT INTO conversations (id) VALUES ('conv')")
            connection.exec_driver_sql(
                "INSERT INTO notifications (id,user_id,run_id,kind,conversation_id,message_id,title,body,created_revision) "
                "VALUES ('notice','user','run','run_completed','conv','message','标题','正文',1)"
            )
            connection.exec_driver_sql("DELETE FROM conversations WHERE id = 'conv'")
            assert connection.scalar(select(Notification.id)) is None
            migration.downgrade()
            assert inspect(connection).get_table_names() == ["conversations", "users"]
    engine.dispose()
