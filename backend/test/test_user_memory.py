"""用户记忆：注入渲染、用户侧管理接口与迁移。"""

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.ai.prompts.system_prompt import assemble_system_prompt
from app.db.database import Base
from app.db.models import Conversation, User, UserMemory
from app.schemas.response import ApiException
from app.services.user_memory_service import MAX_MEMORY_PROMPT_CHARS, UserMemoryService
from app.utils.time import utc_now


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[User.__table__, Conversation.__table__, UserMemory.__table__])
    with Session(engine) as session:
        session.add_all(
            [
                User(id="user-1", username="one"),
                User(id="user-2", username="two"),
                User(id="guest", username="guest", is_guest=True),
            ]
        )
        session.flush()
        session.add(Conversation(id="conv-1", user_id="user-1", title="t", model_id="m"))
        session.commit()
        yield session
    engine.dispose()


def add_memory(db, memory_id, category, content, *, user_id="user-1", source="conv-1", minutes_ago=0):
    moment = utc_now() - timedelta(minutes=minutes_ago)
    db.add(
        UserMemory(
            id=memory_id,
            user_id=user_id,
            category=category,
            content=content,
            source_conversation_id=source,
            created_at=moment,
            updated_at=moment,
        )
    )
    db.commit()


def user(db, user_id="user-1"):
    return db.get(User, user_id)


def test_render_groups_by_category_and_skips_guest_or_disabled(db):
    add_memory(db, "m1", "interests", "喜欢徒步", minutes_ago=3)
    add_memory(db, "m2", "health", "2 型糖尿病，在控糖", minutes_ago=2)
    add_memory(db, "m3", "residence", "杭州西湖区文三路附近", minutes_ago=1)
    add_memory(db, "other-user", "name", "别人", user_id="user-2", source=None)
    service = UserMemoryService(db)

    assert service.render_for_prompt(user(db)) == (
        "- residence: 杭州西湖区文三路附近\n- health: 2 型糖尿病，在控糖\n- interests: 喜欢徒步"
    )
    assert service.render_for_prompt(user(db, "guest")) is None
    assert service.render_for_prompt(None) is None

    service.set_enabled("user-1", False)
    db.expire_all()
    assert service.render_for_prompt(user(db)) is None


def test_render_keeps_most_recent_items_within_budget(db):
    long_text = "很长的记忆" * 40
    for index in range(20):
        add_memory(db, f"m{index}", "other", f"{index}-{long_text}", minutes_ago=100 - index)

    rendered = UserMemoryService(db).render_for_prompt(user(db))

    assert len(rendered) <= MAX_MEMORY_PROMPT_CHARS
    assert "19-" in rendered
    assert "0-" not in rendered.split("\n")[0]


def test_memory_section_injected_after_user_preferences(db):
    assembly = assemble_system_prompt(user_system_prompt="回答简短", user_memory="- residence: 杭州")

    section_ids = assembly.metadata["section_ids"]
    assert section_ids.index("user_memory") > section_ids.index("user_preferences")
    assert "- residence: 杭州" in assembly.messages[-1]["content"]
    assert "user_memory" not in assemble_system_prompt(user_memory="  ").metadata["section_ids"]


def test_conversation_delete_keeps_memory_without_source(db):
    add_memory(db, "m1", "residence", "杭州")
    db.query(Conversation).filter(Conversation.id == "conv-1").delete(synchronize_session=False)
    db.commit()
    db.expire_all()

    assert db.get(UserMemory, "m1").source_conversation_id is None


@pytest.fixture
def client(db):
    from app.api.deps import get_db, require_registered_user
    from app.api.memories import router

    app = FastAPI()
    app.include_router(router, prefix="/api/memories")

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    @app.exception_handler(ApiException)
    async def handle_api_error(request: Request, error: ApiException):
        return JSONResponse(status_code=error.status_code, content={"code": error.code, "message": error.message})

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_registered_user] = lambda: SimpleNamespace(id="user-1", memory_enabled=True)
    with TestClient(app) as test_client:
        yield test_client


def test_api_lists_deletes_clears_and_toggles(client, db):
    add_memory(db, "m1", "interests", "喜欢徒步", minutes_ago=2)
    add_memory(db, "m2", "residence", "杭州", minutes_ago=1)
    add_memory(db, "theirs", "name", "别人", user_id="user-2", source=None)

    listed = client.get("/api/memories").json()["data"]
    assert listed["enabled"] is True
    assert [item["id"] for item in listed["items"]] == ["m2", "m1"]

    assert client.delete("/api/memories/theirs").status_code == 404
    assert client.delete("/api/memories/m1").status_code == 200
    assert client.delete("/api/memories/m1").status_code == 404

    assert client.put("/api/memories/settings", json={"enabled": False}).json()["data"] == {"enabled": False}
    db.expire_all()
    assert user(db).memory_enabled is False

    assert client.delete("/api/memories").json()["data"] == {"deleted": 1}
    assert db.get(UserMemory, "theirs") is not None


def test_migration_round_trip():
    path = Path(__file__).parents[1] / "alembic/versions/c5e1a7d3f924_add_user_memories.py"
    spec = importlib.util.spec_from_file_location("user_memory_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE conversations (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO users (id) VALUES ('user')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            schema = inspect(connection)
            assert connection.exec_driver_sql("SELECT memory_enabled FROM users").scalar() in (1, True)
            references = {row["referred_table"]: row for row in schema.get_foreign_keys("user_memories")}
            assert references["users"]["options"]["ondelete"] == "CASCADE"
            assert references["conversations"]["options"]["ondelete"] == "SET NULL"
            migration.downgrade()
            assert "user_memories" not in inspect(connection).get_table_names()
