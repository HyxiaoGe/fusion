"""更新日志原子广播、不可变版本、用户已读隔离与迁移兼容回归。"""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.changelog_repository import ChangelogRepository
from app.db.database import Base
from app.db.models import AgentSession, Changelog, Conversation, Notification, NotificationUserState, User
from app.db.notification_repository import NotificationRepository
from app.db.repositories import ConversationRepository
from app.schemas.changelog import ChangelogPublishRequest
from app.schemas.response import ApiException
from app.services.changelog_service import ChangelogService
from app.services.notification_service import NotificationService, enqueue_run_notification
from app.utils.time import utc_now

PAYLOAD = {
    "version": "v1.2.3",
    "title": "通知中心更新",
    "summary": "支持统一查看通知",
    "content": "## 改进\n\n支持 **通知中心**。",
}


def make_engine(url="sqlite://", **kwargs):
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                User(id="admin", username="admin", is_superuser=True),
                User(id="user-1", username="one"),
                User(id="user-2", username="two"),
            ]
        )
        db.commit()
    return engine


@pytest.fixture
def db():
    engine = make_engine(connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with Session(engine) as session:
        yield session
    engine.dispose()


def publish(db, **changes):
    return ChangelogService(db).publish("admin", ChangelogPublishRequest(**(PAYLOAD | changes)))


def test_publication_broadcasts_one_copy_per_existing_user_with_independent_reads(db):
    changelog = publish(db)
    assert db.scalar(select(func.count()).select_from(Changelog)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == 3
    assert ChangelogRepository(db).recipient_ids() == ["admin", "user-1", "user-2"]
    first = NotificationService(db).list_notifications("user-1")
    second = NotificationService(db).list_notifications("user-2")
    assert first.revision == second.revision == 1
    assert first.unread_count == second.unread_count == 1
    assert first.unread_conversation_ids == []
    assert first.items[0].business_type == "changelog"
    assert first.items[0].kind == "changelog_published"
    assert first.items[0].target.model_dump() == {"type": "changelog", "changelog_id": changelog.id}
    assert first.items[0].id != second.items[0].id
    assert NotificationService(db).mark_read("user-1", [second.items[0].id]).updated_count == 0
    assert NotificationService(db).mark_read("user-1", [first.items[0].id]).unread_count == 0
    assert NotificationService(db).list_notifications("user-2").unread_count == 1


def test_same_version_retries_are_idempotent_and_new_users_do_not_receive_old_unread(db):
    first = publish(db)
    db.add(User(id="new", username="new"))
    db.commit()
    second = publish(db)
    assert second.id == first.id
    assert second.published_at == first.published_at
    assert db.scalar(select(func.count()).select_from(Notification)) == 3
    assert db.get(NotificationUserState, "admin").revision == 1
    history = ChangelogService(db).get_changelog("new", first.id)
    assert history.content == PAYLOAD["content"]
    assert history.notification_id is None
    assert NotificationService(db).list_notifications("new").unread_count == 0
    assert ChangelogService(db).list_changelogs().items[0].id == first.id


@pytest.mark.parametrize("field", ["title", "summary", "content"])
def test_same_version_different_payload_conflicts_without_changing_anything(db, field):
    first = publish(db)
    with pytest.raises(ApiException) as error:
        publish(db, **{field: "不同内容"})
    assert error.value.status_code == 409
    assert ChangelogService(db).get_changelog("admin", first.id).model_dump() == first.model_dump()
    assert db.scalar(select(func.count()).select_from(Notification)) == 3


def test_details_are_read_only_and_notification_id_is_scoped_to_user(db):
    published = publish(db)
    first = ChangelogService(db).get_changelog("user-1", published.id)
    second = ChangelogService(db).get_changelog("user-2", published.id)
    assert first.notification_id != second.notification_id != published.notification_id
    assert db.get(Notification, first.notification_id).user_id == "user-1"
    assert db.get(Notification, second.notification_id).user_id == "user-2"
    assert NotificationService(db).list_notifications("user-1").unread_count == 1
    assert NotificationService(db).mark_all_read("user-1", 1).updated_count == 1
    assert ChangelogService(db).get_changelog("user-1", published.id).notification_id == first.notification_id
    assert NotificationService(db).list_notifications("user-2").unread_count == 1


def test_broadcast_failure_rolls_back_body_notifications_and_revisions(db, monkeypatch):
    original = NotificationRepository.enqueue_changelog

    def fail_on_last(self, *, user_id, **values):
        if user_id == "user-2":
            raise RuntimeError("模拟投递失败")
        return original(self, user_id=user_id, **values)

    with monkeypatch.context() as scoped:
        scoped.setattr(NotificationRepository, "enqueue_changelog", fail_on_last)
        with pytest.raises(RuntimeError, match="投递失败"):
            publish(db)
    assert db.scalar(select(func.count()).select_from(Changelog)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert db.scalar(select(func.count()).select_from(NotificationUserState)) == 0
    publish(db)
    assert db.scalar(select(func.count()).select_from(Notification)) == 3


@pytest.mark.parametrize("same_version", [True, False])
def test_concurrent_publication_has_no_duplicate_or_missing_recipients(tmp_path, same_version):
    engine = make_engine(
        f"sqlite:///{tmp_path / 'publications.db'}", connect_args={"check_same_thread": False, "timeout": 10}
    )
    barrier = Barrier(2)

    def worker(index):
        with Session(engine) as db:
            barrier.wait(timeout=10)
            return publish(db, version=PAYLOAD["version"] if same_version else f"v{index}").id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(worker, [1, 2]))
        with Session(engine) as db:
            expected = 1 if same_version else 2
            assert len(set(ids)) == expected
            assert db.scalar(select(func.count()).select_from(Changelog)) == expected
            assert db.scalar(select(func.count()).select_from(Notification)) == expected * 3
            assert all(state.revision == expected for state in db.scalars(select(NotificationUserState)))
    finally:
        engine.dispose()


def test_mixed_ai_and_changelog_total_count_read_result_and_source_deletion(db):
    changelog = publish(db)
    db.add(Conversation(id="conv", user_id="user-1", title="会话", model_id="test"))
    db.flush()
    run = AgentSession(
        id="run",
        user_id="user-1",
        conversation_id="conv",
        message_id="message",
        model_id="test",
        provider="test",
        status="completed",
        terminal_at=utc_now(),
    )
    db.add(run)
    db.flush()
    enqueue_run_notification(db, run)
    db.commit()
    service = NotificationService(db)
    page = service.list_notifications("user-1")
    assert page.unread_count == 2
    assert page.unread_conversation_ids == ["conv"]
    assert {item.business_type for item in page.items} == {"changelog", "ai_conversation"}
    result = service.mark_result_read(
        "user-1", conversation_id="conv", results=[{"run_id": "run", "message_id": "message"}]
    )
    assert result.unread_count == 1
    assert result.unread_conversation_ids == []
    assert ConversationRepository(db).delete("conv", "user-1")
    remaining = service.list_notifications("user-1")
    assert remaining.unread_count == 1
    assert remaining.items[0].target.changelog_id == changelog.id
    assert ChangelogService(db).get_changelog("user-1", changelog.id).content == PAYLOAD["content"]


def test_changelog_pagination_does_not_leak_body(db):
    ids = [publish(db, version=f"v{index}").id for index in range(3)]
    service = ChangelogService(db)
    first = service.list_changelogs(limit=1)
    second = service.list_changelogs(cursor=first.next_cursor, limit=1)
    third = service.list_changelogs(cursor=second.next_cursor, limit=1)
    assert [first.items[0].id, second.items[0].id, third.items[0].id] == ids[::-1]
    assert third.next_cursor is None
    assert "content" not in first.items[0].model_dump()
    assert "notification_id" not in first.items[0].model_dump()
    with pytest.raises(ApiException) as error:
        service.list_changelogs(cursor="unknown")
    assert error.value.status_code == 400


@pytest.mark.parametrize("source", ["missing", "mixed", "unknown-business", "wrong-kind", "foreign-changelog"])
def test_database_rejects_invalid_notification_sources(db, source):
    changelog = publish(db)
    db.add(User(id="new", username="new"))
    db.commit()
    notice = Notification(
        id="invalid",
        user_id="new",
        business_type="changelog",
        kind="changelog_published",
        changelog_id=changelog.id,
        title="标题",
        body="正文",
        created_revision=2,
    )
    if source == "missing":
        notice.changelog_id = None
    elif source == "mixed":
        notice.run_id = "fake-run"
    elif source == "unknown-business":
        notice.business_type = "unknown"
    elif source == "wrong-kind":
        notice.kind = "run_failed"
    elif source == "foreign-changelog":
        notice.changelog_id = "missing-source"
    db.add(notice)
    with pytest.raises(IntegrityError) as error:
        db.flush()
    assert ("FOREIGN KEY" if source == "foreign-changelog" else "ck_notifications_source") in str(error.value)
    db.rollback()


def test_database_enforces_one_changelog_notification_per_user(db):
    changelog = publish(db)
    db.add(
        Notification(
            id="duplicate",
            user_id="admin",
            business_type="changelog",
            kind="changelog_published",
            changelog_id=changelog.id,
            title="标题",
            body="正文",
            created_revision=2,
        )
    )
    with pytest.raises(IntegrityError, match="notifications.user_id, notifications.changelog_id"):
        db.flush()
    db.rollback()


def test_postgresql_version_insert_and_source_constraints_are_supported():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    ChangelogRepository(db).insert_publication(PAYLOAD)
    statement = db.scalar.call_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (version) DO NOTHING RETURNING changelogs.id" in sql
    ddl = str(CreateTable(Notification.__table__).compile(dialect=postgresql.dialect()))
    assert "CONSTRAINT ck_notifications_source CHECK" in ddl
    assert "CONSTRAINT uq_notifications_user_changelog UNIQUE (user_id, changelog_id)" in ddl
    assert "FOREIGN KEY(changelog_id) REFERENCES changelogs (id)" in ddl


@pytest.fixture
def client(db):
    # 不进入 TestClient context，真实 app lifespan 不运行，不启动调度器或外部依赖。
    from app.api.deps import get_current_user, get_db
    from main import app

    client = TestClient(app)
    client.current_user = SimpleNamespace(id="admin", is_superuser=True)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: client.current_user
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_api_publish_summary_details_and_private_cache(client, db):
    response = client.post("/api/admin/changelogs", json=PAYLOAD)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    data = response.json()["data"]
    client.current_user = SimpleNamespace(id="user-1", is_superuser=False)
    summary = client.get("/api/changelogs?limit=1")
    assert summary.status_code == 200
    assert summary.headers["cache-control"] == "private, no-store"
    assert summary.json()["data"]["items"][0]["id"] == data["id"]
    assert "content" not in summary.json()["data"]["items"][0]
    detail = client.get(f"/api/changelogs/{data['id']}")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.json()["data"]["content"] == PAYLOAD["content"]
    assert detail.json()["data"]["notification_id"] != data["notification_id"]
    assert NotificationService(db).list_notifications("user-1").unread_count == 1
    assert (
        client.post("/api/notifications/read", json={"ids": [detail.json()["data"]["notification_id"]]}).json()["data"][
            "unread_count"
        ]
        == 0
    )


def test_api_normal_user_cannot_publish_and_missing_log_does_not_leak(client):
    client.current_user = SimpleNamespace(id="user-1", is_superuser=False)
    assert client.post("/api/admin/changelogs", json=PAYLOAD).status_code == 403
    assert client.get("/api/changelogs/missing").status_code == 404
    assert client.put("/api/admin/changelogs", json=PAYLOAD).status_code == 405


def test_api_preserves_markdown_indentation_and_exact_retry_payload(client, db):
    content = "    print(1)\n"
    payload = PAYLOAD | {
        "version": " v1.2.3 ",
        "title": " 通知中心更新 ",
        "summary": " 支持统一查看通知 ",
        "content": content,
    }
    first = client.post("/api/admin/changelogs", json=payload)
    assert first.status_code == 200
    data = first.json()["data"]
    assert data["content"] == content
    assert data["version"] == PAYLOAD["version"]
    assert data["title"] == PAYLOAD["title"]
    assert data["summary"] == PAYLOAD["summary"]
    assert db.get(Changelog, data["id"]).content == content
    second = client.post("/api/admin/changelogs", json=payload)
    assert second.status_code == 200
    assert second.json()["data"]["id"] == data["id"]
    assert client.post("/api/admin/changelogs", json=payload | {"content": content.strip()}).status_code == 409
    client.current_user = SimpleNamespace(id="user-1", is_superuser=False)
    detail = client.get(f"/api/changelogs/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["content"] == content
    assert db.scalar(select(func.count()).select_from(Notification)) == 3


def test_api_requires_login_for_body_and_list(client):
    from app.api.deps import get_current_user

    del client.app.dependency_overrides[get_current_user]
    assert client.get("/api/changelogs").status_code == 401
    assert client.get("/api/changelogs/id").status_code == 401
    assert client.post("/api/admin/changelogs", json=PAYLOAD).status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "bad version"},
        {"version": "v" * 65},
        {"title": " "},
        {"summary": ""},
        {"content": " "},
        {"content": "\n\t    \r\n"},
        {"content": "x" * 100_001},
        {"content": "    " + "x" * 99_997},
        {"extra": "unsupported"},
    ],
)
def test_api_validates_publication_payload(client, changes):
    assert client.post("/api/admin/changelogs", json=PAYLOAD | changes).status_code == 422


def load_migration(name):
    path = Path(__file__).parents[1] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_ai_rows_and_revision_on_sqlite_upgrade():
    old = load_migration("6a1e9f3c8b20_add_notifications.py")
    new = load_migration("b8e2f4a6d0c1_add_changelog_notifications.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE conversations (id VARCHAR PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            old.upgrade()
            connection.exec_driver_sql("INSERT INTO users (id) VALUES ('user')")
            connection.exec_driver_sql("INSERT INTO conversations (id) VALUES ('conv')")
            connection.exec_driver_sql("INSERT INTO notification_user_states (user_id,revision) VALUES ('user',3)")
            connection.exec_driver_sql(
                "INSERT INTO notifications (id,user_id,run_id,kind,conversation_id,message_id,title,body,created_revision,read_at) VALUES ('old','user','run','run_completed','conv','message','标题','正文',2,'2026-10-07')"
            )
            new.upgrade()
            row = connection.exec_driver_sql(
                "SELECT business_type,changelog_id,created_revision,read_at FROM notifications"
            ).one()
            assert row == ("ai_conversation", None, 2, "2026-10-07")
            assert connection.exec_driver_sql("SELECT revision FROM notification_user_states").scalar_one() == 3
            checks = {item["name"] for item in inspect(connection).get_check_constraints("notifications")}
            assert "ck_notifications_source" in checks
            new.downgrade()
            assert "business_type" not in {col["name"] for col in inspect(connection).get_columns("notifications")}
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM notifications").scalar_one() == 1
    engine.dispose()


def test_migration_postgresql_upgrade_ddl():
    migration = load_migration("b8e2f4a6d0c1_add_changelog_notifications.py")
    output = StringIO()
    with Operations.context(
        MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    ):
        migration.upgrade()
    sql = output.getvalue()
    assert "ALTER TABLE notifications ALTER COLUMN run_id DROP NOT NULL" in sql
    assert "ADD CONSTRAINT uq_notifications_user_changelog UNIQUE (user_id, changelog_id)" in sql
    assert "ADD CONSTRAINT ck_notifications_source CHECK" in sql


def test_migration_refuses_to_drop_published_content_on_downgrade():
    old = load_migration("6a1e9f3c8b20_add_notifications.py")
    new = load_migration("b8e2f4a6d0c1_add_changelog_notifications.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE conversations (id VARCHAR PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            old.upgrade()
            new.upgrade()
            connection.exec_driver_sql(
                "INSERT INTO changelogs (id,version,title,summary,content) VALUES ('log','v1','标题','摘要','正文')"
            )
            with pytest.raises(RuntimeError, match="不能无损降级"):
                new.downgrade()
            assert connection.exec_driver_sql("SELECT content FROM changelogs WHERE id='log'").scalar_one() == "正文"
            assert "business_type" in {column["name"] for column in inspect(connection).get_columns("notifications")}
    engine.dispose()
