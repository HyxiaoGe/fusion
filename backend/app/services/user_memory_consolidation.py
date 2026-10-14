"""后台整理用户记忆（「做梦」）：对话闲置后读一遍新增消息，由模型决定增改删画像条目。

每 5 分钟由调度器触发一轮，多 worker 用 Redis 锁保证同时只跑一份。只处理最后一条消息
已闲置 MEMORY_IDLE_MINUTES、且有未整理消息的对话；游客和关闭记忆的用户跳过。
代码只校验返回格式与归属，不判断内容该不该记——那是整理提示词交给模型的事。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.llm_manager import llm_manager
from app.ai.llm_observability import merge_litellm_kwargs
from app.ai.prompts import prompt_manager
from app.core.revocation import get_redis
from app.db.database import SessionLocal
from app.db.models import Conversation, MemoryConsolidationCursor, Message, User, UserMemory
from app.services.user_memory_service import MAX_MEMORY_CONTENT_CHARS
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

# 整理模型不跟随对话模型，保证记忆质量不随用户选的模型波动；默认开推理。
MEMORY_MODEL_ID = "deepseek-chat"
MEMORY_IDLE_MINUTES = 30
# 只回看近几天闲置的对话：上线时不回溯全部历史，长期停滞的对话也不再处理。
MEMORY_LOOKBACK_DAYS = 3
MEMORY_BATCH_SIZE = 20
MAX_FAILED_ATTEMPTS = 3
MAX_MEMORY_ITEMS = 40

_TIMEOUT_SECONDS = 120
_MAX_TOKENS = 4096
_LOCK_KEY = "user_memory_consolidation:lock"
# 一轮最多新开 4 分钟的整理，加上最后一次模型调用的超时仍在锁有效期内；跑完主动释放。
_RUN_BUDGET_SECONDS = 240
_LOCK_TTL_SECONDS = 600
_MAX_USER_MESSAGE_CHARS = 2000
_MAX_ASSISTANT_MESSAGE_CHARS = 1000
_MAX_CONVERSATION_CHARS = 16000
_CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class PendingConversation:
    conversation_id: str
    user_id: str
    last_sequence: int


class MemoryConsolidationOutputError(ValueError):
    """模型返回无法解析或不合约定的内容。"""


def find_pending_conversations(db: Session, *, now: datetime | None = None) -> list[PendingConversation]:
    current = now or utc_now()
    latest = (
        db.query(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.sequence).label("last_sequence"),
            func.max(Message.created_at).label("last_at"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = (
        db.query(Conversation.id, Conversation.user_id, latest.c.last_sequence)
        .join(latest, latest.c.conversation_id == Conversation.id)
        .join(User, User.id == Conversation.user_id)
        .outerjoin(MemoryConsolidationCursor, MemoryConsolidationCursor.conversation_id == Conversation.id)
        .filter(
            User.is_guest.is_(False),
            User.memory_enabled.is_(True),
            latest.c.last_at <= current - timedelta(minutes=MEMORY_IDLE_MINUTES),
            latest.c.last_at >= current - timedelta(days=MEMORY_LOOKBACK_DAYS),
            latest.c.last_sequence > func.coalesce(MemoryConsolidationCursor.processed_sequence, 0),
        )
        .order_by(latest.c.last_at.asc())
        .limit(MEMORY_BATCH_SIZE)
        .all()
    )
    return [PendingConversation(row[0], row[1], int(row[2])) for row in rows]


def _message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()


def render_conversation(messages: list[Message]) -> str:
    """用户原话与（截断后的）回答；超出总长时保留最近的部分。"""
    lines: list[str] = []
    for message in messages:
        text = _message_text(message.content)
        if not text:
            continue
        if message.role == "user":
            lines.append(f"User: {text[:_MAX_USER_MESSAGE_CHARS]}")
        elif message.role == "assistant":
            lines.append(f"Assistant: {text[:_MAX_ASSISTANT_MESSAGE_CHARS]}")
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) > _MAX_CONVERSATION_CHARS:
            break
        kept.append(line)
        used += len(line) + 2
    return "\n\n".join(reversed(kept))


def render_memories(memories: list[UserMemory]) -> str:
    if not memories:
        return "(none)"
    return "\n".join(f"{row.id} | {row.category} | {row.content}" for row in memories)


def parse_operations(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemoryConsolidationOutputError("记忆整理返回的不是 JSON") from exc
    operations = data.get("operations") if isinstance(data, dict) else None
    if not isinstance(operations, list):
        raise MemoryConsolidationOutputError("记忆整理返回缺少 operations")
    return [operation for operation in operations if isinstance(operation, dict)]


def _clean_category(value: Any) -> str | None:
    category = str(value or "").strip().lower()
    if (
        not category
        or len(category) > 32
        or not all(char.isascii() and (char.isalnum() or char == "_") for char in category)
    ):
        return None
    return category


def _clean_content(value: Any) -> str | None:
    content = " ".join(str(value or "").split())
    if not content or len(content) > MAX_MEMORY_CONTENT_CHARS:
        return None
    return content


def apply_operations(
    db: Session,
    *,
    user_id: str,
    conversation_id: str,
    operations: list[dict[str, Any]],
) -> dict[str, int]:
    """按归属与格式应用操作；不合格的单条跳过，不影响其余操作。"""
    existing = {row.id: row for row in db.query(UserMemory).filter(UserMemory.user_id == user_id).all()}
    applied = {"add": 0, "update": 0, "delete": 0, "skipped": 0}
    for operation in operations:
        op = operation.get("op")
        target = existing.get(str(operation.get("id") or ""))
        if op == "delete" and target is not None:
            db.delete(target)
            existing.pop(target.id)
            applied["delete"] += 1
            continue
        category = _clean_category(operation.get("category"))
        content = _clean_content(operation.get("content"))
        if op == "update" and target is not None and content is not None:
            target.content = content
            if category is not None:
                target.category = category
            target.source_conversation_id = conversation_id
            applied["update"] += 1
            continue
        if op == "add" and category is not None and content is not None and len(existing) < MAX_MEMORY_ITEMS:
            row = UserMemory(
                user_id=user_id, category=category, content=content, source_conversation_id=conversation_id
            )
            db.add(row)
            db.flush()
            existing[row.id] = row
            applied["add"] += 1
            continue
        applied["skipped"] += 1
    return applied


async def _call_model(prompt: str, prompt_metadata: dict) -> str:
    model, _, kwargs = llm_manager.resolve_model(MEMORY_MODEL_ID)
    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        max_tokens=_MAX_TOKENS,
        timeout=_TIMEOUT_SECONDS,
        **merge_litellm_kwargs("consolidate_user_memory", dict(kwargs or {}), prompt_metadata=prompt_metadata),
    )
    return response.choices[0].message.content or ""


def _advance_cursor(db: Session, conversation_id: str, *, sequence: int, failed: bool) -> None:
    cursor = db.get(MemoryConsolidationCursor, conversation_id)
    if cursor is None:
        cursor = MemoryConsolidationCursor(conversation_id=conversation_id, processed_sequence=0, failed_attempts=0)
        db.add(cursor)
    if failed:
        cursor.failed_attempts = (cursor.failed_attempts or 0) + 1
        if cursor.failed_attempts < MAX_FAILED_ATTEMPTS:
            return
    cursor.processed_sequence = sequence
    cursor.failed_attempts = 0


async def consolidate_conversation(
    pending: PendingConversation,
    *,
    session_factory=SessionLocal,
    call_model=_call_model,
    now: datetime | None = None,
) -> dict[str, int] | None:
    db = session_factory()
    try:
        cursor = db.get(MemoryConsolidationCursor, pending.conversation_id)
        processed = cursor.processed_sequence if cursor else 0
        messages = (
            db.query(Message)
            .filter(
                Message.conversation_id == pending.conversation_id,
                Message.sequence > processed,
                Message.sequence <= pending.last_sequence,
            )
            .order_by(Message.sequence.asc())
            .all()
        )
        conversation = render_conversation(messages)
        if not conversation:
            _advance_cursor(db, pending.conversation_id, sequence=pending.last_sequence, failed=False)
            db.commit()
            return None
        memories = (
            db.query(UserMemory)
            .filter(UserMemory.user_id == pending.user_id)
            .order_by(UserMemory.created_at.asc(), UserMemory.id.asc())
            .all()
        )
        today = (now or datetime.now(_CHINA_TZ)).astimezone(_CHINA_TZ).date().isoformat()
        prompt, prompt_metadata = prompt_manager.format_prompt_with_metadata(
            "consolidate_user_memory",
            today=today,
            memories=render_memories(memories),
            conversation=conversation,
        )
        db.commit()  # 调模型前释放读事务，不在长调用期间占着连接上的事务。
        try:
            operations = parse_operations(await call_model(prompt, prompt_metadata))
        except Exception as exc:
            db.rollback()
            _advance_cursor(db, pending.conversation_id, sequence=pending.last_sequence, failed=True)
            db.commit()
            logger.warning(
                "user_memory_consolidation_failed conversation=%s error=%s",
                pending.conversation_id,
                type(exc).__name__,
            )
            return None
        # 整理期间用户可能关掉了记忆，写入前再确认一次。
        user = db.get(User, pending.user_id)
        if user is None or user.is_guest or not user.memory_enabled:
            db.rollback()
            return None
        applied = apply_operations(
            db,
            user_id=pending.user_id,
            conversation_id=pending.conversation_id,
            operations=operations,
        )
        _advance_cursor(db, pending.conversation_id, sequence=pending.last_sequence, failed=False)
        db.commit()
        logger.info("user_memory_consolidated conversation=%s applied=%s", pending.conversation_id, applied)
        return applied
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def consolidate_user_memories(
    *,
    session_factory=SessionLocal,
    call_model=_call_model,
    budget_seconds: float = _RUN_BUDGET_SECONDS,
) -> int:
    db = session_factory()
    try:
        pending = find_pending_conversations(db)
    finally:
        db.close()
    deadline = time.monotonic() + budget_seconds
    processed = 0
    for item in pending:
        if time.monotonic() >= deadline:
            break
        try:
            await consolidate_conversation(item, session_factory=session_factory, call_model=call_model)
            processed += 1
        except Exception:
            logger.exception("user_memory_consolidation_error conversation=%s", item.conversation_id)
    return processed


async def consolidate_user_memories_job() -> None:
    """定时任务入口：拿到锁才执行，失败只记日志。"""
    redis = get_redis()
    token = uuid.uuid4().hex
    try:
        if not await asyncio.to_thread(redis.set, _LOCK_KEY, token, nx=True, ex=_LOCK_TTL_SECONDS):
            return
    except Exception:
        logger.exception("user_memory_consolidation_lock_failed")
        return
    try:
        await consolidate_user_memories()
    except Exception:
        logger.exception("user_memory_consolidation_job_failed")
    finally:
        try:
            current = await asyncio.to_thread(redis.get, _LOCK_KEY)
            if current in (token, token.encode()):
                await asyncio.to_thread(redis.delete, _LOCK_KEY)
        except Exception:
            logger.warning("user_memory_consolidation_unlock_failed", exc_info=True)
