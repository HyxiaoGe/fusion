"""工具执行结果记录。

本模块只承载单次工具执行的结果形态和轻量格式化行为，不做 Redis、DB、
LLM 调用或事件发送。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

if TYPE_CHECKING:
    from app.schemas.chat import ContentBlock
    from app.services.tool_handlers.base import BaseToolHandler, ToolResult

TOOL_RESULT_UNAVAILABLE_CONTEXT = render_runtime_prompt("tool_result.unavailable")
TOOL_RESULT_REUSED_CONTEXT = render_runtime_prompt("tool_result.reused")


@dataclass
class ToolExecutionRecord:
    """单次工具执行记录，避免调用方依赖裸 tuple 位置。"""

    tool_call: dict
    result: ToolResult
    handler: BaseToolHandler | None
    block_id: str
    log_id: str
    reused: bool = False

    @property
    def tool_name(self) -> str:
        """返回 LLM tool_call 中声明的工具名，保持 step 统计语义不变。"""
        return str(self.tool_call.get("name", ""))

    def format_llm_context(self, *, citation_numbers: list[int] | None = None) -> str:
        """格式化注入下一轮 LLM 的工具上下文。"""
        if self.reused:
            return TOOL_RESULT_REUSED_CONTEXT
        repair_context = _format_argument_repair_context(self.result)
        if repair_context is not None:
            return repair_context
        if self.handler is None:
            return TOOL_RESULT_UNAVAILABLE_CONTEXT
        if citation_numbers is None or not getattr(self.handler, "supports_run_level_citations", False):
            return self.handler.format_llm_context(self.result)
        return self.handler.format_llm_context(self.result, citation_numbers=citation_numbers)

    def build_content_block(self) -> ContentBlock | None:
        """构造可落库的工具结果 content block。"""
        if self.reused or self.handler is None:
            return None
        return self.handler.build_content_block(self.result, self.block_id, self.log_id)


def _format_argument_repair_context(result: ToolResult) -> str | None:
    data = getattr(result, "data", None)
    repair = data.get("repair") if isinstance(data, dict) else None
    if not isinstance(repair, dict):
        return None
    safe_repair = _safe_repair_projection(
        repair,
        allow_values=data.get("repair_values_verified") is True,
    )
    error_code = data.get("error_code")
    safe_error_code = (
        error_code
        if isinstance(error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
        else "invalid_arguments"
    )
    payload = json.dumps(
        {
            "tool_result": "argument_repair_required",
            "error_code": safe_error_code,
            "repair": safe_repair,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    if safe_repair.get("retryable") is True and safe_repair.get("action") == "provide_argument":
        instruction = render_runtime_prompt("tool_result.repair_verified")
    elif safe_repair.get("retryable") is True:
        instruction = render_runtime_prompt("tool_result.repair_schema")
    elif safe_repair.get("requires_user_input") is True:
        instruction = render_runtime_prompt("tool_result.repair_user")
    else:
        instruction = render_runtime_prompt("tool_result.repair_ended")
    return render_runtime_prompt("tool_result.repair_wrapper", instruction=instruction, payload=payload)


def _safe_repair_projection(
    repair: dict,
    *,
    allow_values: bool,
) -> dict:
    """只向模型投影固定修参字段，禁止第三方 payload 借 repair 通道透传。"""

    action = repair.get("action")
    safe_action = action if action in {"provide_argument", "rebuild_arguments"} else "rebuild_arguments"
    required_fields = [
        field
        for field in repair.get("required_fields", [])
        if isinstance(field, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}", field)
    ][:8]
    allowed_values: dict[str, list[str | int | float | bool | None]] = {}
    raw_allowed = repair.get("allowed_values")
    if isinstance(raw_allowed, dict) and allow_values:
        for field in required_fields:
            values = raw_allowed.get(field)
            if not isinstance(values, list):
                continue
            safe_values = [
                value
                for value in values[:8]
                if value is None
                or isinstance(value, bool)
                or (isinstance(value, (int, float)) and not isinstance(value, bool))
                or (isinstance(value, str) and len(value) <= 80)
            ]
            if safe_values:
                allowed_values[field] = safe_values
    return {
        "action": safe_action,
        "required_fields": required_fields,
        "allowed_values": allowed_values,
        "retryable": repair.get("retryable") is True,
        "requires_user_input": repair.get("requires_user_input") is True,
        "max_attempts": 1,
    }
