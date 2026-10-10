"""knowledge_search：把会话选中的知识库作为 Agent 工具公告，由模型决定何时检索、检索什么。

检索范围只取本轮会话选中的知识库，模型无法通过参数扩大范围；命中正文按不可信材料注入，
来源定位以 knowledge_evidence 块落库（不保存分块正文），引用编号与网页来源统一分配。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.db.knowledge_repository import KnowledgeRepository
from app.schemas.chat import KnowledgeEvidenceBlock, KnowledgeSourceReference
from app.schemas.knowledge import KnowledgeRetrievalRequest
from app.schemas.response import ApiException
from app.services.knowledge.evidence import KNOWLEDGE_SEARCH_TOOL_NAME, knowledge_evidence_id
from app.services.knowledge.service import KnowledgeService
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

KNOWLEDGE_SEARCH_TOP_K = 8
MAX_KNOWLEDGE_CONTEXT_CHARS = 30_000
MAX_KNOWLEDGE_CHUNK_CONTEXT_CHARS = 6_000
MAX_KNOWLEDGE_QUERY_CHARS = 4_000


@dataclass(frozen=True)
class KnowledgeBaseScope:
    id: str
    name: str


@dataclass(frozen=True)
class KnowledgeToolSet:
    handlers: dict[str, BaseToolHandler]
    bases: tuple[KnowledgeBaseScope, ...]
    definitions_factory: Callable[[], list[dict]] = field(default=lambda: [])


def load_knowledge_tool_set(
    db: Session,
    *,
    user_id: str,
    knowledge_base_ids: list[str],
    session_factory: Callable[[], Session],
) -> KnowledgeToolSet | None:
    """在调用线程读取所选知识库名称，产出可在配置线程消费的纯数据。"""

    if not knowledge_base_ids:
        return None
    rows = {row.id: row for row in KnowledgeRepository(db).get_knowledge_bases_by_ids(user_id, knowledge_base_ids)}
    bases = tuple(
        KnowledgeBaseScope(id=base_id, name=rows[base_id].name) for base_id in knowledge_base_ids if base_id in rows
    )
    if not bases:
        return None
    handler = KnowledgeSearchHandler(user_id=user_id, bases=bases, session_factory=session_factory)
    return KnowledgeToolSet(
        handlers={KNOWLEDGE_SEARCH_TOOL_NAME: handler},
        bases=bases,
        definitions_factory=lambda: [build_knowledge_search_tool(bases)],
    )


def build_knowledge_search_tool(bases: tuple[KnowledgeBaseScope, ...]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": KNOWLEDGE_SEARCH_TOOL_NAME,
            "description": render_runtime_prompt(
                "knowledge.tool_description",
                knowledge_base_names=[base.name for base in bases],
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "maxLength": MAX_KNOWLEDGE_QUERY_CHARS,
                        "description": render_runtime_prompt("knowledge.tool_query"),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


class KnowledgeSearchHandler(BaseToolHandler):
    supports_run_level_citations = True

    def __init__(
        self,
        *,
        user_id: str,
        bases: tuple[KnowledgeBaseScope, ...],
        session_factory: Callable[[], Session],
        service_factory: Callable[[Session], Any] = KnowledgeService,
    ):
        self.user_id = user_id
        self.bases = bases
        self.session_factory = session_factory
        self.service_factory = service_factory

    @property
    def tool_name(self) -> str:
        return KNOWLEDGE_SEARCH_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "knowledge"

    async def execute(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "").strip()
        base_ids = [base.id for base in self.bases]
        data: dict[str, Any] = {"query": query, "knowledge_base_ids": base_ids, "hits": []}
        if not query:
            return ToolResult(status="failed", data={**data, "error_code": "empty_query"}, error_message="query 为空")
        db = self.session_factory()
        try:
            # 只检索已有就绪文档的知识库；全部未就绪时如实报告，不替模型编造结果。
            ready_base_ids = {
                row.document.knowledge_base_id
                for row in KnowledgeRepository(db).get_ready_documents(self.user_id, base_ids)
            }
            searchable_ids = [base_id for base_id in base_ids if base_id in ready_base_ids]
            if not searchable_ids:
                return ToolResult(
                    status="failed",
                    data={**data, "error_code": "knowledge_not_ready"},
                    error_message="所选知识库还没有可检索的文档",
                )
            retrieval = await self.service_factory(db).retrieve(
                self.user_id,
                KnowledgeRetrievalRequest(
                    knowledge_base_ids=searchable_ids,
                    query=query[:MAX_KNOWLEDGE_QUERY_CHARS],
                    top_k=KNOWLEDGE_SEARCH_TOP_K,
                ),
            )
        except ApiException as error:
            logger.warning("知识库检索失败: code=%s status=%s", error.code, error.status_code)
            return ToolResult(
                status="failed",
                data={**data, "error_code": str(error.code)},
                error_message=_public_error_message(error),
            )
        except ValidationError:
            return ToolResult(status="failed", data={**data, "error_code": "invalid_query"}, error_message="query 无效")
        finally:
            db.close()
        hits = _select_context_hits(retrieval.hits)
        return ToolResult(status="success" if hits else "degraded", data={**data, "hits": hits})

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> KnowledgeEvidenceBlock | None:
        del log_id
        if result.status == "failed":
            return None
        hits = result.data.get("hits") or []
        return KnowledgeEvidenceBlock(
            type="knowledge_evidence",
            id=block_id,
            query=result.data["query"],
            status="success" if hits else "empty",
            source_count=len(hits),
            knowledge_base_ids=list(result.data["knowledge_base_ids"]),
            # 编号先按命中顺序占位，工具轮会换成与网页来源统一的编号。
            source_refs=[_source_reference(hit, citation_index=index) for index, hit in enumerate(hits, 1)],
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        data = result.data or {}
        if result.status == "failed":
            return render_runtime_prompt("knowledge.search_failed", reason=result.error_message or "unknown")
        hits = data.get("hits") or []
        if not hits:
            return render_runtime_prompt("knowledge.search_empty", query=data.get("query", ""))
        parts = [render_runtime_prompt("knowledge.context_rules")]
        for index, hit in enumerate(hits):
            citation_index = (
                citation_numbers[index]
                if citation_numbers is not None and index < len(citation_numbers) and citation_numbers[index] > 0
                else index + 1
            )
            ref = _source_reference(hit, citation_index=citation_index)
            parts.append(_format_untrusted_knowledge_context(ref, context_text=hit["context_text"]))
        return "\n\n".join(parts)

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        # 工具日志与轨迹只留定位，不永久保存用户文档正文；删除文档后正文不应还能查到。
        data = dict(result.data or {})
        data["hits"] = [
            {key: value for key, value in hit.items() if key != "context_text"} for hit in data.get("hits") or []
        ]
        return data

    def trajectory_output_data(self, result: ToolResult) -> dict:
        return self.sanitize_output_data_for_log(result)

    def _build_result_summary(self, result: ToolResult) -> dict:
        hits = (result.data or {}).get("hits") or []
        if result.status == "failed":
            return {"kind": "knowledge", "truncated": False}
        return {
            "kind": "knowledge",
            "count": len(hits),
            "title": hits[0]["filename"] if hits else "",
            "truncated": False,
        }


def _public_error_message(error: ApiException) -> str:
    if error.status_code in {404, 409}:
        return "所选知识库当前不可检索"
    return "知识库检索服务暂时不可用"


def _select_context_hits(hits: list[Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    consumed = 0
    for hit in hits:
        identity = (str(hit.knowledge_base_id), str(hit.document_id), str(hit.index_version), str(hit.chunk_id))
        text = str(hit.text or "")
        if identity in seen or not text:
            continue
        remaining = MAX_KNOWLEDGE_CONTEXT_CHARS - consumed
        if remaining <= 0:
            break
        context_text = text[: min(MAX_KNOWLEDGE_CHUNK_CONTEXT_CHARS, remaining)]
        consumed += len(context_text)
        seen.add(identity)
        source = hit.source if isinstance(hit.source, dict) else {}
        selected.append(
            {
                "knowledge_base_id": hit.knowledge_base_id,
                "knowledge_base_name": hit.knowledge_base_name,
                "document_id": hit.document_id,
                "index_version": hit.index_version,
                "chunk_id": hit.chunk_id,
                "ordinal": hit.ordinal,
                "filename": hit.filename,
                "page": source.get("page"),
                "section": source.get("section"),
                "char_start": int(source.get("char_start") or 0),
                "char_end": int(source.get("char_end") or 0),
                "similarity": hit.similarity,
                "context_text": context_text,
            }
        )
    return selected


def _source_reference(hit: dict[str, Any], *, citation_index: int) -> KnowledgeSourceReference:
    return KnowledgeSourceReference(
        kind="knowledge",
        evidence_id=knowledge_evidence_id(hit),
        citation_index=citation_index,
        knowledge_base_id=hit["knowledge_base_id"],
        knowledge_base_name=hit["knowledge_base_name"],
        document_id=hit["document_id"],
        index_version=hit["index_version"],
        chunk_id=hit["chunk_id"],
        ordinal=hit["ordinal"],
        filename=hit["filename"],
        page=hit.get("page"),
        section=hit.get("section"),
        char_start=hit["char_start"],
        char_end=hit["char_end"],
        status="success",
    )


def _format_untrusted_knowledge_context(ref: KnowledgeSourceReference, *, context_text: str) -> str:
    attrs = (
        f'evidence_id="{escape(ref.evidence_id)}" '
        f'citation_index="{ref.citation_index}" '
        f'knowledge_base_id="{escape(ref.knowledge_base_id)}" '
        f'document_id="{escape(ref.document_id)}" '
        f'chunk_id="{escape(ref.chunk_id)}"'
    )
    return render_runtime_prompt(
        "knowledge.context",
        citation_index=ref.citation_index,
        attrs=attrs,
        knowledge_base_name=escape(ref.knowledge_base_name),
        filename=escape(ref.filename),
        content=escape(context_text),
    )
