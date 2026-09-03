"""产品结果回答校验的低基数观测。

`repair_unsupported_product_answer()` 会直接改写模型输出（切分句、删表格、重写标签）。
改写是否改坏过答案在线上不可观测：校验只返回稳定 reason code，不持有模型原文，所以
没有任何数据能证明误伤率。这里在改写默认关闭期间记录判定结果与"本应改写"的反事实，
为后续决定是否恢复拦截提供依据。

只输出固定分类与计数，不记录模型原文、用户原文或任何工具返回正文。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logger import app_logger as logger

LOG_PREFIX = "PRODUCT_ANSWER_VALIDATION"

# reason code → 规则类别，便于聚合时按类别看误判分布。
_REASON_CODE_CATEGORIES: dict[str, str] = {
    "ok": "valid",
    "empty_answer": "shape",
    "unsupported_format": "shape",
    "missing_product_result": "shape",
    "unsupported_claim": "risk_term",
    "unsupported_place_relation": "relation",
    "missing_place_relation_caveat": "relation",
    "unknown_line": "unknown_entity",
    "unknown_route_entity": "unknown_entity",
    "unknown_travel_number": "unknown_entity",
    "unknown_travel_entity": "unknown_entity",
    "unknown_travel_time": "unknown_entity",
    "unknown_travel_date": "unknown_entity",
    "unknown_place": "unknown_entity",
    "numeric_mismatch": "numeric",
    "candidate_fact_mismatch": "numeric",
    "weather_fact_mismatch": "weather",
}


def resolve_reason_category(reason_code: str) -> str:
    if reason_code in _REASON_CODE_CATEGORIES:
        return _REASON_CODE_CATEGORIES[reason_code]
    if reason_code.startswith("weather"):
        return "weather"
    return "other"


def build_product_answer_observation(
    *,
    reason_code: str,
    repair_enabled: bool,
    repair_available: bool,
    repair_reason_code: str | None,
    product_result_types: list[str],
) -> dict[str, Any]:
    """组装一条可聚合记录；字段全部是固定分类或布尔值。"""

    return {
        "reason_code": reason_code,
        "reason_category": resolve_reason_category(reason_code),
        "is_valid": reason_code == "ok",
        "repair_enabled": repair_enabled,
        # 改写关闭时仍计算反事实：本应被改写的回答有多少，是恢复拦截与否的判断依据。
        "repair_available": repair_available,
        "repair_applied": repair_enabled and repair_available,
        "repair_reason_code": repair_reason_code or "",
        "product_result_types": sorted(set(product_result_types)),
    }


def emit_product_answer_observation(payload: dict[str, Any] | None) -> None:
    if payload is None:
        return
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        logger.info(f"{LOG_PREFIX} {serialized}")
    except Exception:
        logger.warning("产品结果回答校验观测日志写入失败，已忽略")
