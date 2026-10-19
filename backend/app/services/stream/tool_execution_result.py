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
        argument_error_context = _format_argument_error_context(self.result)
        if argument_error_context is not None:
            return argument_error_context
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


def _format_argument_error_context(result: ToolResult) -> str | None:
    """参数不合法未执行：把字段级错误和已知取值提示原样交还模型，不附加流程指令。"""

    data = getattr(result, "data", None)
    if not isinstance(data, dict) or not isinstance(data.get("argument_errors"), list):
        return None
    error_code = data.get("error_code")
    payload: dict = {
        "error_code": (
            error_code
            if isinstance(error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
            else "invalid_arguments"
        ),
        "argument_errors": [item for item in data["argument_errors"] if isinstance(item, str)][:8],
    }
    hints = data.get("argument_hints")
    if isinstance(hints, dict) and hints:
        payload["argument_hints"] = hints
    return render_runtime_prompt(
        "tool_result.argument_error",
        payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
