"""交付物文档工具：create_document / edit_document。

两个工具都有写入副作用，按 Run 绑定会话与用户归属构造，不自动重试；
工具参数里的正文不进日志和进度事件，只记录长度。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import DocumentBlock
from app.schemas.document import DocumentSource
from app.services.documents.service import (
    MAX_DOCUMENT_CONTENT_CHARS,
    MAX_DOCUMENT_EDITS,
    MAX_DOCUMENT_TITLE_CHARS,
    DocumentEdit,
    DocumentError,
    DocumentService,
    DocumentVersionSnapshot,
    DocumentWriteContext,
    run_document_operation,
)
from app.services.documents.sources import document_sources_from_blocks
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

CREATE_DOCUMENT_TOOL_NAME = "create_document"
EDIT_DOCUMENT_TOOL_NAME = "edit_document"
DOCUMENT_TOOL_NAMES = frozenset({CREATE_DOCUMENT_TOOL_NAME, EDIT_DOCUMENT_TOOL_NAME})


@dataclass(frozen=True)
class DocumentToolBinding:
    """服务端为本 Run 绑定的写入归属；模型参数无法改变会话或用户。"""

    conversation_id: str
    user_id: str
    session_factory: Callable[[], Session]
    message_id: str | None = None
    run_id: str | None = None

    def write_context(self) -> DocumentWriteContext:
        return DocumentWriteContext(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            message_id=self.message_id,
            run_id=self.run_id,
        )


def build_create_document_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": CREATE_DOCUMENT_TOOL_NAME,
            "description": render_runtime_prompt("documents.create_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "maxLength": MAX_DOCUMENT_TITLE_CHARS,
                        "description": render_runtime_prompt("documents.create_title"),
                    },
                    "content": {
                        "type": "string",
                        "maxLength": MAX_DOCUMENT_CONTENT_CHARS,
                        "description": render_runtime_prompt("documents.create_content"),
                    },
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
        },
    }


def build_edit_document_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": EDIT_DOCUMENT_TOOL_NAME,
            "description": render_runtime_prompt("documents.edit_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": render_runtime_prompt("documents.edit_document_id"),
                    },
                    "edits": {
                        "type": "array",
                        "maxItems": MAX_DOCUMENT_EDITS,
                        "description": render_runtime_prompt("documents.edit_edits"),
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": render_runtime_prompt("documents.edit_old_text"),
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": render_runtime_prompt("documents.edit_new_text"),
                                },
                            },
                            "required": ["old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    },
                    "content": {
                        "type": "string",
                        "maxLength": MAX_DOCUMENT_CONTENT_CHARS,
                        "description": render_runtime_prompt("documents.edit_content"),
                    },
                    "title": {
                        "type": "string",
                        "maxLength": MAX_DOCUMENT_TITLE_CHARS,
                        "description": render_runtime_prompt("documents.edit_title"),
                    },
                    "change_summary": {
                        "type": "string",
                        "description": render_runtime_prompt("documents.change_summary"),
                    },
                },
                "required": ["document_id", "change_summary"],
                "additionalProperties": False,
            },
        },
    }


class _DocumentToolHandler(BaseToolHandler):
    # 写入有副作用：不自动重试，也不做运行内成功调用复用。
    supports_automatic_retry = False
    operation: str = ""

    def __init__(self, binding: DocumentToolBinding):
        self.binding = binding

    @property
    def sse_event_prefix(self) -> str:
        return "document"

    async def execute(self, args: dict) -> ToolResult:
        return await self._execute(args, sources=[])

    async def execute_with_runtime_context(self, args: dict, runtime_context: Any) -> ToolResult:
        # 数据来源只取本 Run 已投影的结构化工具结果；模型无法自报来源。
        return await self._execute(args, sources=_collect_sources(runtime_context))

    async def _execute(self, args: dict, *, sources: list[DocumentSource]) -> ToolResult:
        try:
            snapshot = await asyncio.to_thread(
                run_document_operation,
                self.binding.session_factory,
                lambda service: self._run(service, args, sources),
            )
        except DocumentError as error:
            return ToolResult(
                status="failed",
                data={
                    "error_code": error.code,
                    "error_detail": error.detail,
                    "edit_index": error.edit_index,
                    "retryable": False,
                },
                error_message=f"文档操作失败: {error.code}",
            )
        return ToolResult(status="success", data=_snapshot_data(snapshot, operation=self.operation))

    def _run(self, service: DocumentService, args: dict, sources: list[DocumentSource]) -> DocumentVersionSnapshot:
        raise NotImplementedError

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> DocumentBlock | None:
        if result.status != "success":
            return None
        data = result.data
        return DocumentBlock(
            type="document",
            id=block_id,
            schema_version=1,
            document_id=data["document_id"],
            version=data["version"],
            title=data["title"],
            format="markdown",
            operation=self.operation,
            change_summary=data.get("change_summary"),
            char_count=data["char_count"],
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status != "success":
            return render_runtime_prompt(
                "documents.failed_context",
                error_code=data.get("error_code") or "document_operation_failed",
                detail=data.get("error_detail") or "",
                edit_index=data.get("edit_index"),
            )
        key = "documents.created_context" if self.operation == "created" else "documents.edited_context"
        return render_runtime_prompt(
            key,
            document_id=data["document_id"],
            version=data["version"],
            title=data["title"],
            char_count=data["char_count"],
        )

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        return _argument_summary(input_params)

    def sanitize_input_params_for_event(self, input_params: dict) -> dict:
        return _argument_summary(input_params)

    def _build_result_summary(self, result: ToolResult) -> dict:
        if result.status != "success":
            return {"kind": "document", "truncated": False}
        data = result.data
        return {
            "kind": "document",
            "document_id": data["document_id"],
            "version": data["version"],
            "title": data["title"],
            "char_count": data["char_count"],
            "truncated": False,
        }


class CreateDocumentHandler(_DocumentToolHandler):
    operation = "created"

    @property
    def tool_name(self) -> str:
        return CREATE_DOCUMENT_TOOL_NAME

    def _run(self, service: DocumentService, args: dict, sources: list[DocumentSource]) -> DocumentVersionSnapshot:
        return service.create(
            self.binding.write_context(),
            title=args.get("title"),
            content=args.get("content"),
            sources=sources,
        )


class EditDocumentHandler(_DocumentToolHandler):
    operation = "edited"

    @property
    def tool_name(self) -> str:
        return EDIT_DOCUMENT_TOOL_NAME

    def _run(self, service: DocumentService, args: dict, sources: list[DocumentSource]) -> DocumentVersionSnapshot:
        return service.edit(
            self.binding.write_context(),
            document_id=args.get("document_id"),
            edits=_parse_edits(args.get("edits")),
            content=args.get("content"),
            title=args.get("title"),
            change_summary=args.get("change_summary"),
            sources=sources,
        )


def build_document_tool_handlers(binding: DocumentToolBinding) -> dict[str, BaseToolHandler]:
    return {
        CREATE_DOCUMENT_TOOL_NAME: CreateDocumentHandler(binding),
        EDIT_DOCUMENT_TOOL_NAME: EditDocumentHandler(binding),
    }


def _collect_sources(runtime_context: Any) -> list[DocumentSource]:
    try:
        return document_sources_from_blocks(getattr(runtime_context, "content_blocks", ()) or ())
    except Exception as error:  # noqa: BLE001 — 来源采集失败不能阻断文档写入
        logger.warning("文档数据来源采集失败: error_type=%s", type(error).__name__)
        return []


def _parse_edits(value: Any) -> list[DocumentEdit] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise DocumentError("edits_invalid")
    edits: list[DocumentEdit] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("old_text"), str)
            or not isinstance(item.get("new_text"), str)
        ):
            raise DocumentError("edit_invalid", edit_index=index)
        edits.append(DocumentEdit(old_text=item["old_text"], new_text=item["new_text"]))
    return edits


def _snapshot_data(snapshot: DocumentVersionSnapshot, *, operation: str) -> dict:
    return {
        "document_id": snapshot.document_id,
        "version": snapshot.version,
        "title": snapshot.title,
        "operation": operation,
        "change_summary": snapshot.change_summary,
        "char_count": len(snapshot.content),
        "source_count": len(snapshot.sources),
    }


def _argument_summary(args: dict) -> dict:
    """只保留可审计的形状信息，正文不进入日志、轨迹或进度事件。"""

    if not isinstance(args, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("document_id", "title", "change_summary"):
        value = args.get(key)
        if isinstance(value, str):
            summary[key] = value[:200]
    content = args.get("content")
    if isinstance(content, str):
        summary["content_chars"] = len(content)
    edits = args.get("edits")
    if isinstance(edits, list):
        summary["edit_count"] = len(edits)
    return summary
