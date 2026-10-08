"""本轮不允许调用工具时的请求形态。

历史里有工具事务时，请求不带工具定义会出问题：mimo-v2.6-flash 在「无 tools +
历史有 tool 消息」时会返回空回答；把历史工具事务去掉又会让上下文在轮间骤降。
所以禁止调用的轮次照常带上工具定义，用 tool_choice="none" 禁止调用。

实测多数模型遵守禁止，mimo-v2.6-flash 在成文轮约三分之一仍返回工具调用。
这类回合按结果判定（不按模型名）：丢弃该轮，退回不带工具、去掉历史工具事务的
请求形态重做一次。
"""

from typing import Any

TOOL_CHOICE_NONE = "none"


def forbids_tool_calls(call_kwargs: Any) -> bool:
    return isinstance(call_kwargs, dict) and call_kwargs.get("tool_choice") == TOOL_CHOICE_NONE


def callable_tools(call_kwargs: Any) -> list:
    """本轮模型可以调用的工具定义；禁止调用的轮次只公告定义，不算可调用。"""
    if not isinstance(call_kwargs, dict) or forbids_tool_calls(call_kwargs):
        return []
    return list(call_kwargs.get("tools") or [])


def forbid_tool_calls(call_kwargs: dict, tools: list) -> dict:
    return {**call_kwargs, "tools": list(tools), "tool_choice": TOOL_CHOICE_NONE}


def without_tool_definitions(call_kwargs: dict) -> dict:
    return {key: value for key, value in call_kwargs.items() if key not in ("tools", "tool_choice")}


def ignored_tool_ban(*, tool_calls: Any, finish_reason: str | None) -> bool:
    """禁止调用工具的轮次仍返回了工具调用（含正文里的工具协议）。"""
    return bool(tool_calls) or finish_reason == "tool_protocol_error"
