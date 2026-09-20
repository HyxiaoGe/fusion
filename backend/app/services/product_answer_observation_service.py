"""按明确分母聚合已留存的产品回答决策。"""

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.product_answer_observation_repository import query_product_answer_observations
from app.utils.time import as_utc


def observation_time_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """无时区输入按 Asia/Shanghai 解释，数据库查询统一 UTC。"""
    start = as_utc(start if start.tzinfo else start.replace(tzinfo=ZoneInfo("Asia/Shanghai")))
    end = as_utc(end if end.tzinfo else end.replace(tzinfo=ZoneInfo("Asia/Shanghai")))
    if start >= end:
        raise ValueError("观测开始时间必须早于结束时间")
    return start, end


def _fraction(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else None,
    }


def aggregate_product_answer_observations(db: Session, start: datetime, end: datetime) -> dict:
    start, end = observation_time_range(start, end)
    groups, (first, last) = query_product_answer_observations(db, start, end)
    total = validated_count = invalid_count = repair_count = attempted_count = 0
    reasons: Counter = Counter()
    categories: Counter = Counter()
    paths: Counter = Counter()
    category_invalid: Counter = Counter()
    category_repair: Counter = Counter()
    for path, validated, code, category, valid, repair, attempted, count in groups:
        total += count
        paths[path] += count
        attempted_count += count if attempted else 0
        if not validated:
            continue
        validated_count += count
        reasons[code] += count
        categories[category] += count
        invalid_count += count if not valid else 0
        repair_count += count if repair else 0
        category_invalid[category] += count if not valid else 0
        category_repair[category] += count if repair else 0
    return {
        "time_range": {
            "from": start.isoformat(),
            "to_exclusive": end.isoformat(),
            "naive_input_timezone": "Asia/Shanghai",
        },
        "retained_range": {
            "first": as_utc(first).isoformat() if first else None,
            "last": as_utc(last).isoformat() if last else None,
        },
        "coverage": {
            "scope": "deferred_product_answer_decisions",
            "complete": None,
            "unobserved_count": None,
            "note": "仅统计成功留存的产品回答延迟提交决策；不含此前终止/知识库/联网恢复/工具澄清路径。数据库写入失败不在此分母，不能由本查询证明零漏写。",
        },
        "all_observed_decisions": total,
        "validated_decisions": validated_count,
        "not_validated_decisions": total - validated_count,
        "observation_path": dict(sorted(paths.items())),
        "product_tool_attempted": _fraction(attempted_count, total),
        "reason_code": dict(sorted(reasons.items())),
        "reason_category": dict(sorted(categories.items())),
        "invalid_among_validated": _fraction(invalid_count, validated_count),
        "repair_available_among_validated": _fraction(repair_count, validated_count),
        "invalid_among_all_observed": _fraction(invalid_count, total),
        "repair_available_among_all_observed": _fraction(repair_count, total),
        "by_category": {
            category: {
                "validated_decisions": count,
                "invalid": _fraction(category_invalid[category], count),
                "repair_available": _fraction(category_repair[category], count),
            }
            for category, count in sorted(categories.items())
        },
        "interpretation": "未校验不等于通过。repair_available 仅表示改写函数能产出非 None，是潜在改写候选量；不能据此估算误伤率；本工具不判断样本是否足够。",
    }
