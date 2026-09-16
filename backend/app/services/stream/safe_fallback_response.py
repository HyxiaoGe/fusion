"""按冻结用户请求选择安全兜底语言，最终正文只使用本地受控模板。"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field

import litellm

from app.ai.llm_observability import merge_litellm_kwargs
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.services.utility_model import UTILITY_LLM_TIMEOUT, UTILITY_MAX_TOKENS, resolve_utility_model

SUPPORTED_FALLBACK_LOCALES = frozenset({"zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es", "pt", "ru", "ar", "hi"})
_SUPPORTED_REASONS = frozenset({"tool_failure", "no_evidence", "protocol_error"})
_DEFAULT_LOCALE = "zh-CN"


@dataclass(frozen=True)
class FallbackResponseContext:
    """一个 run 冻结的真实请求及语言缓存，不受总结控制消息改写影响。"""

    original_message: str
    preferred_locale: str | None = None
    _locale: str | None = field(default=None, init=False, repr=False, compare=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False, compare=False)


def _render_response(reason: str, locale: str) -> str:
    if reason not in _SUPPORTED_REASONS:
        raise ValueError("未知安全兜底原因")
    return render_runtime_prompt(f"safe_fallback.responses.{locale}.{reason}")


def default_safe_fallback(reason: str) -> str:
    """同步兼容入口，集中读取原有中文默认文案。"""

    return _render_response(reason, _DEFAULT_LOCALE)


def _parse_locale(raw: str) -> str:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"locale"}:
        raise ValueError("语言选择响应格式无效")
    locale = payload["locale"]
    if not isinstance(locale, str) or locale not in SUPPORTED_FALLBACK_LOCALES:
        raise ValueError("语言选择响应不在允许范围内")
    return locale


async def _request_locale(context: FallbackResponseContext, model_id: str, timeout_s: float) -> str:
    # 目录缓存失效时解析可能发起同步 HTTP；移出事件循环，仍受外层完整截止时间约束。
    # 线程只解析配置，取消后的迟到结果不会继续触发模型请求。
    model, _, model_kwargs = await asyncio.to_thread(resolve_utility_model, model_id)
    kwargs = merge_litellm_kwargs("fallback_language", model_kwargs)
    kwargs.update(
        model=model,
        messages=[
            {"role": "system", "content": render_runtime_prompt("safe_fallback.language_selector")},
            {"role": "user", "content": context.original_message},
        ],
        stream=False,
        temperature=0,
        num_retries=0,
        max_tokens=UTILITY_MAX_TOKENS,
        timeout=timeout_s,
        response_format={"type": "json_object"},
    )
    response = await litellm.acompletion(**kwargs)
    return _parse_locale(response.choices[0].message.content)


async def _resolve_locale(context: FallbackResponseContext, model_id: str, timeout_s: float) -> str:
    if context._locale is not None:
        return context._locale
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        await asyncio.wait_for(context._lock.acquire(), timeout=timeout_s)
    except TimeoutError:
        # 另一个同 run 选择仍在运行，当前调用只降级，不覆盖其待写入的缓存。
        logger.warning("安全兜底语言选择失败: error_type=TimeoutError")
        return _DEFAULT_LOCALE
    try:
        if context._locale is not None:
            return context._locale
        remaining = deadline - loop.time()
        try:
            if remaining <= 0:
                raise TimeoutError
            locale = await asyncio.wait_for(_request_locale(context, model_id, remaining), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"安全兜底语言选择失败: error_type={type(error).__name__}")
            locale = _DEFAULT_LOCALE
        object.__setattr__(context, "_locale", locale)
        return locale
    finally:
        context._lock.release()


async def render_safe_fallback(
    reason: str,
    *,
    context: FallbackResponseContext | None,
    model_id: str,
    timeout_s: float,
) -> str:
    """只在安全守卫已触发后调用；未知或超时选择回退原有中文文案。"""

    fallback = default_safe_fallback(reason)
    if context is None:
        return fallback
    if isinstance(context.preferred_locale, str) and context.preferred_locale in SUPPORTED_FALLBACK_LOCALES:
        return _render_response(reason, context.preferred_locale)
    if context._locale is not None:
        return _render_response(reason, context._locale)
    if not context.original_message.strip() or not math.isfinite(timeout_s) or timeout_s <= 0:
        return fallback
    locale = await _resolve_locale(context, model_id, min(timeout_s, UTILITY_LLM_TIMEOUT))
    return _render_response(reason, locale)
