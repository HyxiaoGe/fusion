"""校验模型基于结构化产品结果生成的最终回答。

只做结构与格式校验：回答非空、没有 Markdown 表格、存在产品结果块。
回答内容是否越出结果边界，由工具结果的 limitations 与使用约束提示词前置约束，
不在服务端用正则解析自然语言去猜测回答在说哪条结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProductAnswerValidation:
    """只返回稳定原因码，避免日志或调用方持有模型原文。"""

    is_valid: bool
    reason_code: str


_PRODUCT_RESULT_TYPES = {
    "place_results",
    "route_results",
    "weather_results",
    "flight_results",
    "train_results",
    "itinerary_results",
}
_SEMANTIC_TEXT_RE = re.compile(r"[一-鿿A-Za-z0-9]")
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$",
    re.MULTILINE,
)


def validate_product_answer(answer: str, content_blocks: list[Any]) -> ProductAnswerValidation:
    """校验回答形态；回答内容的事实边界交给前置约束。"""

    normalized_answer = answer.strip() if isinstance(answer, str) else ""
    if not normalized_answer or not _SEMANTIC_TEXT_RE.search(normalized_answer):
        return ProductAnswerValidation(False, "empty_answer")
    if _MARKDOWN_TABLE_SEPARATOR_RE.search(normalized_answer):
        return ProductAnswerValidation(False, "unsupported_format")
    if not any(_value(block, "type") in _PRODUCT_RESULT_TYPES for block in content_blocks):
        return ProductAnswerValidation(False, "missing_product_result")
    return ProductAnswerValidation(True, "ok")


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
