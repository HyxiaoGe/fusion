"""交付物文档的创建、按片段修改与版本读取。

文档属于会话，正文按不可变版本存放；修改只做精确片段替换，找不到或不唯一时
拒绝并把原因返回给模型重试，不做模糊匹配去猜模型想改哪里。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Conversation, Document, DocumentVersion
from app.schemas.document import DocumentSource

MAX_DOCUMENT_TITLE_CHARS = 120
MAX_DOCUMENT_CONTENT_CHARS = 60_000
MAX_DOCUMENT_EDITS = 20
MAX_CHANGE_SUMMARY_CHARS = 200
MAX_DOCUMENT_SOURCES = 40


class DocumentError(Exception):
    """可安全返回给模型或 API 调用方的文档错误；code 稳定，detail 不含正文。"""

    def __init__(self, code: str, detail: str = "", *, edit_index: int | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.edit_index = edit_index


@dataclass(frozen=True)
class DocumentEdit:
    old_text: str
    new_text: str


@dataclass(frozen=True)
class DocumentWriteContext:
    """一次写入的归属与追溯信息；由服务端绑定，不来自模型参数。"""

    conversation_id: str
    user_id: str
    message_id: str | None = None
    run_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class DocumentVersionSnapshot:
    document_id: str
    version: int
    title: str
    format: str
    content: str
    change_summary: str | None
    sources: tuple[DocumentSource, ...]
    created_at: datetime | None


@dataclass(frozen=True)
class DocumentVersionMeta:
    version: int
    title: str
    change_summary: str | None
    char_count: int
    created_at: datetime | None


@dataclass(frozen=True)
class DocumentDetail:
    document_id: str
    conversation_id: str
    title: str
    format: str
    current_version: int
    versions: tuple[DocumentVersionMeta, ...]
    created_at: datetime | None
    updated_at: datetime | None


def normalize_title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentError("title_required")
    title = " ".join(value.split())
    if len(title) > MAX_DOCUMENT_TITLE_CHARS:
        raise DocumentError("title_too_long", f"max {MAX_DOCUMENT_TITLE_CHARS} characters")
    return title


def normalize_content(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentError("content_required")
    content = value.replace("\r\n", "\n").strip("\n") + "\n"
    if len(content) > MAX_DOCUMENT_CONTENT_CHARS:
        raise DocumentError("content_too_long", f"max {MAX_DOCUMENT_CONTENT_CHARS} characters")
    return content


def normalize_change_summary(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DocumentError("change_summary_invalid")
    summary = " ".join(value.split())
    return summary[:MAX_CHANGE_SUMMARY_CHARS] or None


def apply_document_edits(content: str, edits: Sequence[DocumentEdit]) -> str:
    """按顺序做精确替换；每段 old_text 必须在当前正文中恰好出现一次。"""

    if not edits:
        raise DocumentError("edits_required")
    if len(edits) > MAX_DOCUMENT_EDITS:
        raise DocumentError("too_many_edits", f"max {MAX_DOCUMENT_EDITS} edits per call")
    updated = content
    for index, edit in enumerate(edits):
        old_text = edit.old_text.replace("\r\n", "\n")
        if not old_text:
            raise DocumentError("edit_old_text_required", edit_index=index)
        occurrences = updated.count(old_text)
        if occurrences == 0:
            raise DocumentError("edit_old_text_not_found", edit_index=index)
        if occurrences > 1:
            raise DocumentError(
                "edit_old_text_not_unique",
                f"found {occurrences} times; include more surrounding text",
                edit_index=index,
            )
        updated = updated.replace(old_text, edit.new_text.replace("\r\n", "\n"), 1)
    return normalize_content(updated)


class DocumentService:
    """同步 Session 服务；异步调用方通过 run_document_operation 在线程中执行。"""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        context: DocumentWriteContext,
        *,
        title: Any,
        content: Any,
        sources: Sequence[DocumentSource] = (),
    ) -> DocumentVersionSnapshot:
        normalized_title = normalize_title(title)
        normalized_content = normalize_content(content)
        self._require_conversation(context)
        document = Document(
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            title=normalized_title,
            format="markdown",
            current_version=1,
        )
        self.db.add(document)
        self.db.flush()
        version = self._add_version(
            document,
            version=1,
            content=normalized_content,
            change_summary=None,
            sources=sources,
            context=context,
        )
        self.db.commit()
        return _snapshot(document, version)

    def edit(
        self,
        context: DocumentWriteContext,
        *,
        document_id: Any,
        edits: Sequence[DocumentEdit] | None = None,
        content: Any = None,
        title: Any = None,
        change_summary: Any = None,
        sources: Sequence[DocumentSource] = (),
    ) -> DocumentVersionSnapshot:
        if (edits is None) == (content is None):
            raise DocumentError("edit_mode_invalid", "provide exactly one of edits or content")
        document = self._owned_document(document_id, context, for_update=True)
        current = self._version(document.id, document.current_version)
        updated_content = (
            normalize_content(content) if content is not None else apply_document_edits(current.content, edits or ())
        )
        updated_title = normalize_title(title) if title is not None else document.title
        if updated_content == current.content and updated_title == document.title:
            raise DocumentError("edit_no_change")
        next_version = document.current_version + 1
        merged_sources = _merge_sources(_load_sources(current.sources), sources)
        summary = normalize_change_summary(change_summary)
        document.title = updated_title
        document.current_version = next_version
        try:
            version = self._add_version(
                document,
                version=next_version,
                content=updated_content,
                change_summary=summary,
                sources=merged_sources,
                context=context,
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DocumentError("concurrent_edit", "the document changed; read it again and retry") from None
        return _snapshot(document, version)

    def get_detail(self, document_id: str, *, user_id: str) -> DocumentDetail:
        document = self._user_document(document_id, user_id)
        versions = (
            self.db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version.asc())
            .all()
        )
        return DocumentDetail(
            document_id=document.id,
            conversation_id=document.conversation_id,
            title=document.title,
            format=document.format,
            current_version=document.current_version,
            versions=tuple(
                DocumentVersionMeta(
                    version=item.version,
                    title=item.title,
                    change_summary=item.change_summary,
                    char_count=len(item.content),
                    created_at=item.created_at,
                )
                for item in versions
            ),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    def get_version(self, document_id: str, *, user_id: str, version: int | None = None) -> DocumentVersionSnapshot:
        document = self._user_document(document_id, user_id)
        return _snapshot(document, self._version(document.id, version or document.current_version))

    def latest_for_conversation(
        self, conversation_id: str, *, user_id: str, limit: int = 3
    ) -> list[DocumentVersionSnapshot]:
        documents = (
            self.db.query(Document)
            .filter(Document.conversation_id == conversation_id, Document.user_id == user_id)
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
            .all()
        )
        return [_snapshot(document, self._version(document.id, document.current_version)) for document in documents]

    def _require_conversation(self, context: DocumentWriteContext) -> None:
        exists = (
            self.db.query(Conversation.id)
            .filter(Conversation.id == context.conversation_id, Conversation.user_id == context.user_id)
            .first()
        )
        if exists is None:
            raise DocumentError("conversation_not_found")

    def _owned_document(self, document_id: Any, context: DocumentWriteContext, *, for_update: bool) -> Document:
        if not isinstance(document_id, str) or not document_id:
            raise DocumentError("document_not_found")
        query = self.db.query(Document).filter(
            Document.id == document_id,
            Document.conversation_id == context.conversation_id,
            Document.user_id == context.user_id,
        )
        if for_update:
            query = query.with_for_update()
        document = query.first()
        if document is None:
            raise DocumentError("document_not_found")
        return document

    def _user_document(self, document_id: str, user_id: str) -> Document:
        document = self.db.query(Document).filter(Document.id == document_id, Document.user_id == user_id).first()
        if document is None:
            raise DocumentError("document_not_found")
        return document

    def _version(self, document_id: str, version: int) -> DocumentVersion:
        row = (
            self.db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id, DocumentVersion.version == version)
            .first()
        )
        if row is None:
            raise DocumentError("version_not_found")
        return row

    def _add_version(
        self,
        document: Document,
        *,
        version: int,
        content: str,
        change_summary: str | None,
        sources: Sequence[DocumentSource],
        context: DocumentWriteContext,
    ) -> DocumentVersion:
        row = DocumentVersion(
            document_id=document.id,
            version=version,
            title=document.title,
            content=content,
            change_summary=change_summary,
            sources=[source.model_dump(mode="json") for source in list(sources)[:MAX_DOCUMENT_SOURCES]],
            message_id=context.message_id,
            run_id=context.run_id,
            tool_call_id=context.tool_call_id,
        )
        self.db.add(row)
        self.db.flush()
        return row


def _load_sources(raw: Any) -> list[DocumentSource]:
    sources: list[DocumentSource] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            sources.append(DocumentSource.model_validate(item))
        except ValueError:
            continue
    return sources


def _merge_sources(previous: Sequence[DocumentSource], current: Sequence[DocumentSource]) -> list[DocumentSource]:
    """本次查询优先：同一来源取最新一次，保持先后顺序，超出上限丢弃最早的。"""

    merged: dict[tuple[str, str], DocumentSource] = {}
    for source in [*previous, *current]:
        key = (source.kind, source.label)
        merged.pop(key, None)
        merged[key] = source
    return list(merged.values())[-MAX_DOCUMENT_SOURCES:]


def _snapshot(document: Document, version: DocumentVersion) -> DocumentVersionSnapshot:
    return DocumentVersionSnapshot(
        document_id=document.id,
        version=version.version,
        title=version.title,
        format=document.format,
        content=version.content,
        change_summary=version.change_summary,
        sources=tuple(_load_sources(version.sources)),
        created_at=version.created_at,
    )


def run_document_operation(
    session_factory: Callable[[], Session],
    operation: Callable[[DocumentService], Any],
) -> Any:
    """在独立 Session 中执行一次文档操作；供 asyncio.to_thread 调用。"""

    db = session_factory()
    try:
        return operation(DocumentService(db))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
