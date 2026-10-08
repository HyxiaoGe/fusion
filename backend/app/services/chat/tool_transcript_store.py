"""工具调用记录的读写：加载需要回放的历史记录，回答完成后保存本轮记录与截断点。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import app_logger as logger
from app.db.models import Conversation as ConversationModel
from app.db.models import Message as MessageModel
from app.services.chat.tool_transcript import transcript_tool_call_ids


@dataclass(frozen=True)
class ToolTranscriptHistory:
    # assistant 消息 id → 该轮工具记录（已排除截断点及之前的轮次）
    transcripts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 回放的工具调用 id → 所属 assistant 消息 sequence
    sequences: dict[str, int] = field(default_factory=dict)
    cutoff_sequence: int | None = None


def load_tool_transcripts(db: Any, conversation_id: str, assistant_message_ids: Iterable[str]) -> ToolTranscriptHistory:
    message_ids = [message_id for message_id in assistant_message_ids if message_id]
    cutoff = (
        db.query(ConversationModel.tool_transcript_cutoff_sequence)
        .filter(ConversationModel.id == conversation_id)
        .scalar()
    )
    if not message_ids:
        return ToolTranscriptHistory(cutoff_sequence=cutoff)
    query = db.query(MessageModel.id, MessageModel.sequence, MessageModel.tool_transcript).filter(
        MessageModel.conversation_id == conversation_id,
        MessageModel.id.in_(message_ids),
        MessageModel.tool_transcript.isnot(None),
        MessageModel.sequence.isnot(None),
    )
    if cutoff is not None:
        query = query.filter(MessageModel.sequence > cutoff)
    transcripts: dict[str, list[dict[str, Any]]] = {}
    sequences: dict[str, int] = {}
    for message_id, sequence, transcript in query.all():
        if not isinstance(transcript, list) or not transcript:
            continue
        transcripts[message_id] = transcript
        for tool_call_id in transcript_tool_call_ids(transcript):
            sequences[tool_call_id] = sequence
    return ToolTranscriptHistory(transcripts=transcripts, sequences=sequences, cutoff_sequence=cutoff)


def save_tool_transcript(
    db: Any,
    *,
    conversation_id: str,
    message_id: str,
    transcript: list[dict[str, Any]],
    cutoff_sequence: int | None,
) -> bool:
    """回答已落库后写入；失败只影响后续轮次能否回放，不改变本轮结果。"""
    try:
        if transcript:
            db.query(MessageModel).filter(
                MessageModel.id == message_id,
                MessageModel.conversation_id == conversation_id,
            ).update({MessageModel.tool_transcript: transcript}, synchronize_session=False)
        if cutoff_sequence is not None:
            db.query(ConversationModel).filter(ConversationModel.id == conversation_id).filter(
                (ConversationModel.tool_transcript_cutoff_sequence.is_(None))
                | (ConversationModel.tool_transcript_cutoff_sequence < cutoff_sequence)
            ).update(
                {ConversationModel.tool_transcript_cutoff_sequence: cutoff_sequence},
                synchronize_session=False,
            )
        db.commit()
        return True
    except Exception as error:
        db.rollback()
        logger.warning("工具调用记录保存失败: message_id=%s, error_type=%s", message_id, type(error).__name__)
        return False
