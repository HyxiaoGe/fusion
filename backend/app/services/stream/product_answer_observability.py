"""产品结果回答校验的低基数观测。

`repair_unsupported_product_answer()` 会直接改写模型输出（切分句、删表格、重写标签）。
`PRODUCT_ANSWER_REPAIR_ENABLED=False` 只关闭这一步改写，不关闭校验或拦截：
产品回答校验失败时仍会丢弃模型候选，交付基于结构化结果的确定性兜底。
这里记录校验结果与"若启用则可改写"的反事实；这些低基数字段不含人工正确性判断，
不能直接作为误伤率，也不能把改写关闭称为只观测或等待恢复拦截。

只输出固定分类与计数，不记录模型原文、用户原文或任何工具返回正文。
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.logger import app_logger as logger
from app.db.product_answer_observation_repository import persist_product_answer_observation
from app.utils.time import utc_now

LOG_PREFIX = "PRODUCT_ANSWER_VALIDATION"
_STORE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="product-answer-observation")
_STORE_WAIT_SECONDS = 1.0

# reason code → 规则类别，便于聚合时按类别看误判分布。
_REASON_CODE_CATEGORIES: dict[str, str] = {
    "ok": "valid",
    "empty_answer": "shape",
    "unsupported_format": "shape",
    "missing_product_result": "shape",
    "unsupported_claim": "risk_term",
    "unsupported_place_relation": "relation",
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
# 旧路径仅供历史观测记录与统计测试读取；新请求统一走 validated。
_OBSERVATION_PATHS = frozenset(
    {"validated", "no_product_result", "no_usable_evidence", "weather_activity", "mixed_travel", "single_travel_comparison"}
)
_REPAIR_REASON_CODES = frozenset(_REASON_CODE_CATEGORIES) | {"not_repairable", "insufficient_coverage", ""}
_BLOCK_TYPES = frozenset(
    {
        "text",
        "thinking",
        "file",
        "search",
        "url_read",
        "knowledge_evidence",
        "unsupported_result",
        "place_results",
        "route_results",
        "weather_results",
        "flight_results",
        "train_results",
        "itinerary_results",
    }
)


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
    product_tool_attempted: bool = False,
    observation_path: str = "validated",
) -> dict[str, Any]:
    """组装一条可聚合记录；字段全部是固定分类或布尔值。"""

    if observation_path not in _OBSERVATION_PATHS:
        raise ValueError("未知产品回答观测路径")
    validated = observation_path == "validated"
    reason_code = (reason_code if reason_code in _REASON_CODE_CATEGORIES else "other") if validated else "not_validated"
    repair_available = bool(repair_available) if validated else False
    return {
        "observation_path": observation_path,
        "validated": validated,
        "reason_code": reason_code,
        "reason_category": resolve_reason_category(reason_code) if validated else "not_validated",
        "is_valid": reason_code == "ok" if validated else None,
        "repair_enabled": repair_enabled,
        # 改写关闭时仍计算反事实；可改写不等于误伤，校验失败后的拦截始终保留。
        "repair_available": repair_available,
        "repair_applied": repair_enabled and repair_available,
        "repair_reason_code": (repair_reason_code or "")
        if repair_reason_code in _REPAIR_REASON_CODES or repair_reason_code is None
        else "other",
        "product_tool_attempted": bool(product_tool_attempted),
        "product_result_types": sorted({value if value in _BLOCK_TYPES else "other" for value in product_result_types}),
    }


async def retain_product_answer_observation(payload: dict[str, Any]) -> bool:
    """有界等待独立事务提交，隔离数据库卡顿与产品回答交付。"""
    observed_at = utc_now()
    try:
        # 仅执行线程限为两个；已接收记录排队完成，不因第三个并发请求而丢样本。
        future = asyncio.get_running_loop().run_in_executor(
            _STORE_EXECUTOR, persist_product_answer_observation, payload, observed_at
        )
    except Exception:
        logger.error("PRODUCT_ANSWER_OBSERVATION_STORE_FAILED error_type=submit_failed")
        return False

    def completed(late_future: asyncio.Future) -> None:
        # shield 保留已开始事务；读取晚到异常，避免泄漏错误正文到事件循环日志。
        if not late_future.cancelled() and (error := late_future.exception()) is not None:
            logger.error("PRODUCT_ANSWER_OBSERVATION_STORE_FAILED error_type=%s", type(error).__name__)

    future.add_done_callback(completed)
    try:
        await asyncio.wait_for(asyncio.shield(future), timeout=_STORE_WAIT_SECONDS)
        return True
    except TimeoutError:
        logger.error("PRODUCT_ANSWER_OBSERVATION_STORE_PENDING error_type=wait_timeout")
        return False
    except Exception:
        return False


def emit_product_answer_observation(payload: dict[str, Any] | None) -> None:
    if payload is None:
        return
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        logger.info(f"{LOG_PREFIX} {serialized}")
    except Exception:
        logger.warning("产品结果回答校验观测日志写入失败，已忽略")
