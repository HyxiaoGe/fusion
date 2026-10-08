"""供应商推理请求协议。

可见推理的 SSE/持久化由通用流协议负责；这里只处理供应商请求参数兼容，
避免把某个模型的参数限制散落在 Agent 各个阶段。
"""

from __future__ import annotations

from copy import deepcopy

from app.ai.litellm_utils import merge_extra_body
from app.services.stream.tool_ban import callable_tools

_VOLCENGINE_PROVIDERS = frozenset({"volcengine"})


def configure_reasoning_call_kwargs(
    call_kwargs: dict,
    *,
    provider: str | None,
    should_use_reasoning: bool,
    thinking_switchable: bool = False,
) -> dict:
    """按当前回合的真实 tools 状态生成推理请求参数。

    thinking_switchable：模型元数据确认能用 thinking=disabled 真正关掉推理。
    """

    configured = deepcopy(call_kwargs)
    normalized_provider = (provider or "").strip().lower()
    # 禁止调用工具的轮次只公告定义，推理参数按无工具处理（实测各家均接受）。
    has_tools = bool(callable_tools(configured))

    if not should_use_reasoning:
        if not thinking_switchable:
            return configured
        # 用户关了推理就让模型真的不思考，否则模型照常推理、只是前端看不到，白等。
        if normalized_provider == "gemini":
            # Gemini 走 LiteLLM 原生适配：reasoning_effort=none 映射为关闭思考；
            # 透传的 thinking 字段会被 Gemini API 400。
            configured["reasoning_effort"] = "none"
        else:
            # 必须再包一层 extra_body：代理按 OpenAI 参数表校验顶层字段，顶层 thinking
            # 会被拒（UnsupportedParamsError）；内层 extra_body 由代理原样转给供应商。
            merge_extra_body(configured, {"extra_body": {"thinking": {"type": "disabled"}}})
        return configured

    if normalized_provider == "deepseek":
        merge_extra_body(configured, {"thinking": {"type": "enabled"}})
        if has_tools:
            # DeepSeek thinking + tools 支持原生自动决策，但不接受 tool_choice。
            configured.pop("tool_choice", None)
        return configured

    if normalized_provider == "gemini":
        # LiteLLM 会把 Gemini 的 thought summary 统一映射为 reasoning_content。
        configured.setdefault("reasoning_effort", "high")
        return configured

    if normalized_provider in _VOLCENGINE_PROVIDERS:
        if has_tools:
            merge_extra_body(configured, {"thinking": {"type": "disabled"}})
        else:
            _remove_disabled_thinking(configured)
    return configured


def _remove_disabled_thinking(call_kwargs: dict) -> None:
    extra_body = call_kwargs.get("extra_body")
    if not isinstance(extra_body, dict):
        return
    thinking = extra_body.get("thinking")
    if not isinstance(thinking, dict) or thinking.get("type") != "disabled":
        return

    next_extra_body = {key: value for key, value in extra_body.items() if key != "thinking"}
    if next_extra_body:
        call_kwargs["extra_body"] = next_extra_body
    else:
        call_kwargs.pop("extra_body", None)
