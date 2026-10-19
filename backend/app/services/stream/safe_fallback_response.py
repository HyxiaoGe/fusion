"""安全兜底的最终正文，只使用本地受控的中文模板。"""

from __future__ import annotations

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

# 界面只有中文，兜底正文固定用中文，不再额外调用模型判断用户语言。
PROTOCOL_FALLBACK_TEXT = render_runtime_prompt("safe_fallback.protocol_error")
