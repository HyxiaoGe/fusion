"""通过真实 SQLite 和新 Session 验证可靠终止的持久化边界。"""

import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Conversation, Message, User
from app.schemas.chat import SearchBlock, StopStreamRequest, ThinkingBlock
from app.services.chat_service import ChatService
from app.services.stream.agent_loop_run_completion import (
    AgentLoopRunCompletionContext,
    finalize_cancelled_run,
    finalize_failed_run,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.persistence import persist_message


@pytest.fixture
def database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Conversation.__table__, Message.__table__])
    with Session(engine) as db:
        db.add(User(id="user", username="可靠终止测试"))
        db.add(Conversation(id="conv", user_id="user", title="可靠终止", model_id="model"))
        db.add(
            Message(
                id="user-msg", conversation_id="conv", role="user", content=[], sequence=1, generation_task_id="task"
            )
        )
        db.commit()
    yield engine
    engine.dispose()


def _search(block_id="search"):
    return SearchBlock(
        type="search",
        id=block_id,
        query="Fusion",
        sources=[{"title": "示例资料", "url": "https://example.com/evidence"}],
        source_count=1,
    )


def _read_content(engine):
    with Session(engine) as db:
        return db.get(Message, "assistant").content


def _persist(db, blocks, *, partial=True, generation_task_id="task"):
    return persist_message(
        db,
        "assistant",
        "conv",
        "model",
        blocks,
        partial=partial,
        sequence=2,
        generation_task_id=generation_task_id,
        create_after_retry_user_id="user-msg",
    )


def test_server_checkpoint_keeps_new_tool_result_across_sessions(database):
    blocks = [ThinkingBlock(type="thinking", id="thinking", thinking="正在搜索")]
    with Session(database) as db:
        assert _persist(db, blocks) is True
        blocks.append(_search())
        assert _persist(db, blocks) is True
        # 同一 checkpoint 重放不应重复增加卡片。
        assert _persist(db, blocks) is True
    assert [block["type"] for block in _read_content(database)] == ["thinking", "search"]


@pytest.mark.parametrize("outcome", ["cancelled", "failed", "completed"])
@pytest.mark.parametrize("checkpoint_after_tool", [False, True])
def test_run_terminal_persistence_keeps_search_in_new_session(database, outcome, checkpoint_after_tool):
    asyncio.run(_run_terminal_persistence(database, outcome, checkpoint_after_tool))


async def _run_terminal_persistence(database, outcome, checkpoint_after_tool):
    state = AgentLoopState(content_blocks=[ThinkingBlock(type="thinking", id="thinking", thinking="正在搜索")])
    with Session(database) as db:
        assert _persist(db, state.content_blocks) is True
        state.content_blocks.append(_search())
        if checkpoint_after_tool:
            assert _persist(db, state.content_blocks) is True
        context = AgentLoopRunCompletionContext(
            db=db,
            conversation_id="conv",
            task_id="task",
            run_id="run",
            model_id="model",
            provider="test",
            assistant_message_id="assistant",
            assistant_message_sequence=2,
            emitter=SimpleNamespace(plan_snapshot=AsyncMock()),
            session_cache=SimpleNamespace(),
            state=state,
            duration_ms_factory=lambda: 1,
        )
        kwargs = dict(
            context=context,
            persist_message_fn=partial(
                persist_message, generation_task_id="task", create_after_retry_user_id="user-msg"
            ),
            finalize_stream_fn=AsyncMock(),
            warning_fn=lambda _: None,
        )
        if outcome == "cancelled":
            await finalize_cancelled_run(**kwargs, interrupt_agent_run_fn=AsyncMock())
        elif outcome == "failed":
            await finalize_failed_run(**kwargs, fail_agent_run_fn=AsyncMock(), error=RuntimeError("工具失败"))
        else:
            assert _persist(db, state.content_blocks, partial=False) is True
    content = _read_content(database)
    assert [block["type"] for block in content] == ["thinking", "search"]
    assert content[1]["sources"][0]["url"] == "https://example.com/evidence"


def test_client_cannot_inject_search_and_stale_server_cannot_write(database):
    forged = _search("forged")
    with pytest.raises(ValidationError):
        StopStreamRequest(partial_content=[forged.model_dump(mode="json")])
    with Session(database) as db:
        blocks = [ThinkingBlock(type="thinking", id="thinking", thinking="正在搜索")]
        assert _persist(db, blocks) is True
        # 绕过请求模型后，服务层仍必须过滤客户端工具卡片。
        service = ChatService.__new__(ChatService)
        service.db = db
        accepted = service.persist_stream_partial_before_stop(
            conversation_id="conv",
            user_id="user",
            message_id="assistant",
            partial_content=[forged],
            stream_meta={"status": "streaming", "user_id": "user", "message_id": "assistant", "task_id": "task"},
        )
        assert accepted is False
        assert _persist(db, [forged], generation_task_id="stale-task") is False
    assert [block["type"] for block in _read_content(database)] == ["thinking"]
