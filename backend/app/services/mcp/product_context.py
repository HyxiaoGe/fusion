"""产品工具（地图、天气）结果给模型时的通用包装：回答要求 + 不可信数据外壳，按字节上限截断。"""

from __future__ import annotations

import re
from html import escape

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

_PAYLOAD_MARKER = "__FUSION_PAYLOAD__"
_INLINE_SECRET_VALUE = r'"(?:\\.|[^"\\])+"|\'(?:\\.|[^\'\\])+\'|[a-z0-9._~+/=-]{4,}'
_INLINE_SECRET_PATTERN = re.compile(
    rf"(?P<key_prefix>\b(?:api[ _-]*key|client[ _-]*secret|password|access[ _-]*token|token|cookie|session[ _-]*id)\s*[:=]\s*)"
    rf"(?P<key_value>{_INLINE_SECRET_VALUE})"
    rf"|(?P<auth_prefix>\b(?:proxy[ _-]*authorization|authorization)\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?)"
    rf"(?P<auth_value>{_INLINE_SECRET_VALUE})",
    re.IGNORECASE,
)


def redact_product_text(value: str) -> str:
    """模型填的地名等文字里夹带的密钥写法换成 [REDACTED]，再拿去显示或记录。"""
    return _INLINE_SECRET_PATTERN.sub(_redact_secret_match, value[:20_000])


def _redact_secret_match(match: re.Match[str]) -> str:
    prefix = match.group("key_prefix") or match.group("auth_prefix") or ""
    return f"{prefix}[REDACTED]"


def format_product_context(*, tool_name: str, payload_text: str, max_bytes: int, usage_contract: str = "") -> str:
    rendered = (
        usage_contract
        + render_runtime_prompt("product.final_answer")
        + render_runtime_prompt("product.result_wrapper", tool=escape(tool_name), payload=_PAYLOAD_MARKER)
    )
    prefix, marker, suffix = rendered.partition(_PAYLOAD_MARKER)
    if not marker:
        raise ValueError("产品工具 Prompt 缺少 payload 占位符")
    escaped_payload = escape(payload_text, quote=False)
    available = max(0, max_bytes - len(prefix.encode()) - len(suffix.encode()))
    raw = escaped_payload.encode()
    if len(raw) > available:
        truncated = "\n" + render_runtime_prompt("shared.truncated")
        escaped_payload = raw[: max(0, available - len(truncated.encode()))].decode(errors="ignore") + truncated
    return f"{prefix}{escaped_payload}{suffix}"
