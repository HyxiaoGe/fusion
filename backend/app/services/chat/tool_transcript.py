"""每轮回答的工具调用记录：采集、回放与成批截断点。

一轮回答里模型发起的工具调用和收到的工具结果原样保存（不含思考），后续轮次插在该轮
最终回答之前回放，让模型能看到之前读过的资料。超出上下文阈值时由上下文管理先删最早
几轮的工具记录；本模块据此推进会话截断点，此后这些记录不再回放，直到下次超阈值。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

_THINK_SEGMENT = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
# 知识库正文不随会话永久保存（删除文档后不能再被回放），历史轮只留说明，需要时重新检索。
_KNOWLEDGE_SEARCH_TOOL_NAME = "knowledge_search"


def transcript_entries(messages: Iterable[PromptMessage | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把本轮追加的协议消息转成可存储记录，去掉思考内容。"""
    entries: list[dict[str, Any]] = []
    knowledge_call_ids: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            knowledge_call_ids.update(
                str(call.get("id"))
                for call in message.get("tool_calls") or []
                if isinstance(call, Mapping) and _call_name(call) == _KNOWLEDGE_SEARCH_TOOL_NAME
            )
            content = message.get("content")
            entries.append(
                {
                    "role": "assistant",
                    "content": _THINK_SEGMENT.sub("", content).strip() if isinstance(content, str) else "",
                    "tool_calls": [_tool_call_entry(call) for call in message.get("tool_calls") or []],
                }
            )
        elif role == "tool" and message.get("tool_call_id"):
            content = message.get("content")
            if str(message.get("tool_call_id")) in knowledge_call_ids:
                content = render_runtime_prompt("knowledge.result_not_replayed")
            entries.append(
                {
                    "role": "tool",
                    "tool_call_id": str(message.get("tool_call_id")),
                    "content": content if isinstance(content, str) else "",
                }
            )
    return entries


def replay_messages(transcript: Any) -> list[PromptMessage]:
    """把存储记录还原成协议消息；只回放调用与结果一一对应的完整事务。"""
    if not isinstance(transcript, list):
        return []
    replayed: list[PromptMessage] = []
    index = 0
    while index < len(transcript):
        entry = transcript[index]
        index += 1
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        calls = [call for call in entry.get("tool_calls") or [] if _valid_tool_call(call)]
        call_ids = [call["id"] for call in calls]
        if not calls or len(set(call_ids)) != len(call_ids):
            continue
        results: list[PromptMessage] = []
        while index < len(transcript):
            result = transcript[index]
            if not isinstance(result, dict) or result.get("role") != "tool":
                break
            index += 1
            if result.get("tool_call_id") in call_ids and isinstance(result.get("content"), str):
                results.append(
                    PromptMessage(
                        role="tool",
                        content=result["content"],
                        provider_fields={"tool_call_id": result["tool_call_id"]},
                    )
                )
        if sorted(message.get("tool_call_id") for message in results) != sorted(call_ids):
            continue
        content = entry.get("content")
        replayed.append(
            PromptMessage(
                role="assistant",
                content=content if isinstance(content, str) else "",
                provider_fields={"tool_calls": calls},
            )
        )
        replayed.extend(results)
    return replayed


def max_citation_index(content_blocks: Iterable[Any]) -> int:
    """历史回答里已用过的最大引用编号；新一轮从其后编号，避免和回放的工具结果撞号。"""
    highest = 0
    for block in content_blocks or []:
        if _field(block, "type") not in {"search", "url_read", "knowledge_evidence"}:
            continue
        for source in [*(_field(block, "source_refs") or []), *(_field(block, "sources") or [])]:
            index = _field(source, "citation_index")
            if isinstance(index, int) and not isinstance(index, bool):
                highest = max(highest, index)
    return highest


def without_tool_transactions(
    messages: Iterable[PromptMessage | Mapping[str, Any]], tool_call_ids: Iterable[str]
) -> list[PromptMessage | Mapping[str, Any]]:
    """去掉指定工具调用的事务。请求不带工具定义时，回放的历史事务会让部分模型返回空回答。"""
    removed_ids = set(tool_call_ids)
    if not removed_ids:
        return list(messages)
    kept: list[PromptMessage | Mapping[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id") in removed_ids:
            continue
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if calls and all(isinstance(call, Mapping) and call.get("id") in removed_ids for call in calls):
            continue
        kept.append(message)
    return kept


def transcript_tool_call_ids(transcript: Any) -> list[str]:
    """记录里所有工具调用 id，用于判断上下文管理删掉了哪几轮。"""
    return [
        str(entry["tool_call_id"])
        for entry in transcript or []
        if isinstance(entry, dict) and entry.get("role") == "tool" and entry.get("tool_call_id")
    ]


def advance_cutoff(
    current_cutoff: int | None,
    *,
    history_sequences: Mapping[str, int],
    before_messages: Iterable[PromptMessage | Mapping[str, Any]],
    after_messages: Iterable[PromptMessage | Mapping[str, Any]],
) -> int | None:
    """上下文管理删掉了哪些历史工具记录，就把截断点推进到其中最晚的那一轮。"""
    removed_ids = _tool_result_ids(before_messages) - _tool_result_ids(after_messages)
    removed = [sequence for tool_call_id, sequence in history_sequences.items() if tool_call_id in removed_ids]
    if not removed:
        return current_cutoff
    latest_removed = max(removed)
    return latest_removed if current_cutoff is None else max(current_cutoff, latest_removed)


def _tool_result_ids(messages: Iterable[PromptMessage | Mapping[str, Any]]) -> set[str]:
    return {
        str(message.get("tool_call_id"))
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _tool_call_entry(call: Any) -> dict[str, Any]:
    call = call if isinstance(call, Mapping) else {}
    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
    return {
        "id": str(call.get("id") or ""),
        "type": "function",
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": function.get("arguments") if isinstance(function.get("arguments"), str) else "{}",
        },
    }


def _valid_tool_call(call: Any) -> bool:
    if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
        return False
    function = call.get("function")
    return (
        isinstance(function, dict)
        and isinstance(function.get("name"), str)
        and bool(function["name"])
        and isinstance(function.get("arguments"), str)
    )


def _call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
    return str(function.get("name") or call.get("name") or "")
