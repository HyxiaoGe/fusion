"""从轨迹账本提取一次 run 的判分快照。

快照随判分结果一起入库：轨迹表随会话级联删除，复盘不能依赖原会话还在。
工具结果和回答按长度截断，只保留判分与复盘需要的部分。

裁判用的快照把搜索、读页结果整理成模型当时看到的样子：每条来源带回答里用的引用编号，
内容按模型上下文的同一上限截取。轨迹详情存的是原始返回，单条来源可能带几万字原文，
直接截断会把后面的来源整段截掉，裁判就会把有依据的内容判成编造。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentEvent, AgentSession, Message, ToolCallLog
from app.services.tool_handlers.url_read import model_visible_page_content
from app.services.tool_handlers.web_search import SOURCE_CONTEXT_CHARS, _normalize_context_source_limit

TOOL_RESULT_CHAR_LIMIT = 4000
# 裁判核对回答依据时要看到完整搜索结果；单次 web_search 实测可达 5 万字。
JUDGE_TOOL_RESULT_CHAR_LIMIT = 60000
TOOL_ARGUMENT_CHAR_LIMIT = 2000
ANSWER_CHAR_LIMIT = 12000

# 只为渲染存在、不承载回答内容的块
_NON_ANSWER_BLOCKS = frozenset({"thinking"})


def _bounded(value: Any, limit: int) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "chars": len(encoded), "preview": encoded[:limit]}


def _source_refs_by_log_id(answer_content: Any) -> dict[str, list[Mapping[str, Any]]]:
    refs: dict[str, list[Mapping[str, Any]]] = {}
    for block in answer_content or []:
        if isinstance(block, Mapping) and block.get("tool_call_log_id") and isinstance(block.get("source_refs"), list):
            refs[str(block["tool_call_log_id"])] = [ref for ref in block["source_refs"] if isinstance(ref, Mapping)]
    return refs


def _model_view(
    tool: str, payload: Mapping[str, Any], result: Any, refs: list[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """按模型上下文的同一口径整理搜索/读页结果；不是这两种工具返回 None。"""
    if not isinstance(result, Mapping):
        return None
    if tool == "url_read":
        content, truncated = model_visible_page_content(str(result.get("content") or ""), result.get("title"))
        return {
            "citation": refs[0].get("citation_index") if refs else None,
            "url": result.get("url"),
            "title": result.get("title"),
            "content": content,
            "content_truncated": truncated,
        }
    if tool != "web_search":
        return None
    raw_sources = [source for source in result.get("sources") or [] if isinstance(source, Mapping)]
    by_url = {source.get("url"): source for source in raw_sources}
    # source_refs 按模型看到的顺序记录全部来源；轨迹详情超出预算时会缺后面的来源。
    entries = [(ref.get("citation_index"), ref.get("url"), ref.get("title")) for ref in refs] or [
        (None, source.get("url"), source.get("title")) for source in raw_sources
    ]
    context_limit = _normalize_context_source_limit(
        result.get("context_source_limit") or payload.get("context_source_limit")
    )
    sources = []
    for index, (citation, url, title) in enumerate(entries):
        item: dict[str, Any] = {"citation": citation, "url": url, "title": title}
        # source_refs 与原始来源同序；URL 规范化后对不上时按位置对应。
        raw = by_url.get(url) or (raw_sources[index] if index < len(raw_sources) else None)
        if index >= context_limit:
            item["content"] = None
        elif raw is None:
            item["content"] = None
            item["content_unavailable"] = True
        else:
            item["content"] = str(raw.get("content") or raw.get("description") or "")[:SOURCE_CONTEXT_CHARS]
        sources.append(item)
    return {"query": result.get("query") or payload.get("query"), "sources": sources}


def build_snapshot(
    *,
    session: Mapping[str, Any],
    events: Iterable[tuple[str, Mapping[str, Any]]],
    tool_logs: Iterable[Mapping[str, Any]],
    answer_content: Any,
    result_limit: int = TOOL_RESULT_CHAR_LIMIT,
    model_view: bool = False,
) -> dict[str, Any]:
    """events 按 sequence 排好序；tool_logs 每项含 tool_name/status/error_message/detail，可带 id。

    model_view 为 True 时（裁判用），搜索/读页结果整理成模型看到的样子并带引用编号。
    """
    refs_by_log_id = _source_refs_by_log_id(answer_content) if model_view else {}
    mode = None
    skills: list[str] = []
    skills_status = None
    finish_reason = None
    for event_type, payload in events:
        if event_type == "run_started" and mode is None:
            mode = (payload.get("capability_resolution") or {}).get("package_id")
        elif event_type == "skills_resolved":
            skills_status = payload.get("status")
            for item in payload.get("skills") or []:
                name = item.get("skill_id") if isinstance(item, Mapping) else item
                if isinstance(name, str) and name not in skills:
                    skills.append(name)
        elif event_type == "run_completed":
            finish_reason = payload.get("finish_reason")

    tool_calls = []
    for log in tool_logs:
        detail = log.get("detail") or {}
        if log["tool_name"] == "load_skill":
            name = (detail.get("payload") or {}).get("name")
            if isinstance(name, str) and name not in skills:
                skills.append(name)
        result = detail.get("result")
        if model_view and log.get("status") == "success":
            view = _model_view(
                log["tool_name"],
                detail.get("payload") or {},
                result,
                refs_by_log_id.get(str(log.get("id")), []),
            )
            if view is not None:
                result = view
        tool_calls.append(
            {
                "tool": log["tool_name"],
                "status": log.get("status"),
                "error": log.get("error_message"),
                "arguments": _bounded(detail.get("payload") or {}, TOOL_ARGUMENT_CHAR_LIMIT),
                "result": _bounded(result, result_limit),
            }
        )

    blocks = [block for block in answer_content or [] if isinstance(block, Mapping)]
    answer_text = "\n\n".join(
        block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    return {
        "run_id": session.get("id"),
        "conversation_id": session.get("conversation_id"),
        "model_id": session.get("model_id"),
        "status": session.get("status"),
        "limit_reason": session.get("limit_reason"),
        "finish_reason": finish_reason,
        "total_steps": session.get("total_steps"),
        "duration_ms": session.get("total_duration_ms"),
        "mode": mode,
        "skills_status": skills_status,
        "skills": skills,
        "tool_calls": tool_calls,
        "answer_blocks": [block.get("type") for block in blocks if block.get("type") not in _NON_ANSWER_BLOCKS],
        "answer_text": answer_text[:ANSWER_CHAR_LIMIT],
        "answer_truncated": len(answer_text) > ANSWER_CHAR_LIMIT,
    }


def load_snapshot(
    db: Session,
    run_id: str,
    *,
    result_limit: int = TOOL_RESULT_CHAR_LIMIT,
    model_view: bool = False,
) -> dict[str, Any] | None:
    session = db.get(AgentSession, run_id)
    if session is None:
        return None
    events = db.execute(
        select(AgentEvent.event_type, AgentEvent.payload)
        .where(AgentEvent.run_id == run_id)
        .order_by(AgentEvent.sequence)
    ).all()
    logs = (
        db.execute(
            select(ToolCallLog)
            .where(ToolCallLog.trace_id == run_id)
            .order_by(ToolCallLog.step_number, ToolCallLog.created_at, ToolCallLog.id)
        )
        .scalars()
        .all()
    )
    answer = db.get(Message, session.message_id) if session.message_id else None
    return build_snapshot(
        session={
            "id": session.id,
            "conversation_id": session.conversation_id,
            "model_id": session.model_id,
            "status": session.status,
            "limit_reason": session.limit_reason,
            "total_steps": session.total_steps,
            "total_duration_ms": session.total_duration_ms,
        },
        events=[(event_type, payload or {}) for event_type, payload in events],
        tool_logs=[
            {
                "id": log.id,
                "tool_name": log.tool_name,
                "status": log.status,
                "error_message": log.error_message,
                "detail": (log.extra_metadata or {}).get("trajectory_detail") or {},
            }
            for log in logs
        ],
        answer_content=answer.content if answer is not None else None,
        result_limit=result_limit,
        model_view=model_view,
    )
