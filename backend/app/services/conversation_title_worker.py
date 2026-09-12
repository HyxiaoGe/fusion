"""会话标题的并发生成与送达。

标题只由首个用户提问决定，不读 assistant 正文，因此用户消息一落库就能开始算——
不必等正文终态。生成完成时主 SSE 通常仍在流式状态，直接推事件即可，不存在
与封口赛跑的问题（推荐问题依赖正文，才需要封口前送达）。

与推荐问题的后台任务同理：必须使用独立 DB session，不能和主链路共用。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logger import app_logger as logger
from app.db.database import SessionLocal
from app.db.models import Message as MessageModel

SessionFactory = Callable[[], Any]
EmitTitleFn = Callable[..., Awaitable[Any]]

_worker_tasks: set[asyncio.Task] = set()


def schedule_conversation_title_generation(
    *,
    conversation_id: str,
    user_id: str,
    emit_fn: EmitTitleFn | None,
    session_factory: SessionFactory | None = None,
) -> asyncio.Task:
    """脱离聊天 task registry 启动，下一轮消息不会取消它。"""

    factory = session_factory or SessionLocal
    coroutine = run_conversation_title_worker(
        conversation_id=conversation_id,
        user_id=user_id,
        emit_fn=emit_fn,
        session_factory=factory,
    )
    try:
        task = asyncio.create_task(coroutine, name=f"conversation-title:{conversation_id}")
    except Exception:
        coroutine.close()
        raise
    _worker_tasks.add(task)
    task.add_done_callback(_worker_tasks.discard)
    return task


async def run_conversation_title_worker(
    *,
    conversation_id: str,
    user_id: str,
    emit_fn: EmitTitleFn | None,
    session_factory: SessionFactory | None = None,
) -> str | None:
    """仅在会话首轮生成标题并推送；任何失败都不影响正文链路。"""

    factory = session_factory or SessionLocal
    started_at = time.monotonic()
    db = factory()
    try:
        if not _is_first_turn(db, conversation_id):
            return None
        # 延迟导入：chat_service 经 stream.runner 间接依赖本模块，模块级导入会成环。
        from app.services.chat_service import ChatService

        title = await ChatService(db).generate_title(user_id=user_id, conversation_id=conversation_id)
    except asyncio.CancelledError:
        db.rollback()
        raise
    except Exception as error:  # noqa: BLE001 — 标题是辅助能力，失败只记录
        db.rollback()
        logger.warning(
            "会话标题生成失败: conversation_id=%s error_type=%s",
            conversation_id,
            type(error).__name__,
        )
        return None
    finally:
        db.close()

    if not title or emit_fn is None:
        return title

    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
    try:
        await emit_fn(conversation_id=conversation_id, title=title, duration_ms=duration_ms)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — 送达失败时标题已落库，前端走会话列表刷新兜底
        logger.warning(
            "会话标题送达失败: conversation_id=%s error_type=%s",
            conversation_id,
            type(error).__name__,
        )
    return title


def _is_first_turn(db: Any, conversation_id: str) -> bool:
    """首轮才自动定名：标题取自首个提问，后续轮由用户手动重新生成。"""
    user_message_count = (
        db.query(MessageModel)
        .filter(
            MessageModel.conversation_id == conversation_id,
            MessageModel.role == "user",
        )
        .count()
    )
    return user_message_count == 1
