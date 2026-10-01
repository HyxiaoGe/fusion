"""按 Run 装配文档工具：在调用线程读取会话现有文档，绑定写入归属，产出纯数据供配置线程消费。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.documents.service import DocumentService, DocumentVersionSnapshot
from app.services.tool_handlers.document import (
    DocumentToolBinding,
    build_create_document_tool,
    build_document_tool_handlers,
    build_edit_document_tool,
)

# 注入上下文的正文总量上限；最近一份文档总是完整注入，其余放不下时只列标题。
MAX_DOCUMENT_CONTEXT_CHARS = 60_000


@dataclass(frozen=True)
class DocumentToolSet:
    handlers: dict[str, Any]
    existing_documents: tuple[DocumentVersionSnapshot, ...] = ()
    definitions_factory: Callable[[], list[dict]] = field(
        default=lambda: [build_create_document_tool(), build_edit_document_tool()]
    )

    @property
    def existing_titles(self) -> tuple[str, ...]:
        return tuple(document.title for document in self.existing_documents)


def load_document_tool_set(
    db: Session,
    *,
    conversation_id: str,
    user_id: str,
    session_factory: Callable[[], Session],
    message_id: str | None,
    run_id: str | None,
) -> DocumentToolSet:
    existing = DocumentService(db).latest_for_conversation(conversation_id, user_id=user_id)
    binding = DocumentToolBinding(
        conversation_id=conversation_id,
        user_id=user_id,
        session_factory=session_factory,
        message_id=message_id,
        run_id=run_id,
    )
    return DocumentToolSet(handlers=build_document_tool_handlers(binding), existing_documents=tuple(existing))


def render_current_documents_context(documents: tuple[DocumentVersionSnapshot, ...]) -> str | None:
    if not documents:
        return None
    entries = []
    remaining = MAX_DOCUMENT_CONTEXT_CHARS
    for index, document in enumerate(documents):
        include_content = index == 0 or len(document.content) <= remaining
        if include_content:
            remaining = max(0, remaining - len(document.content))
        entries.append(
            {
                "document_id": document.document_id,
                "version": document.version,
                "title": document.title,
                "content": document.content if include_content else None,
            }
        )
    return render_runtime_prompt("documents.current_context", documents=entries)
