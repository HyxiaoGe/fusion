"""触顶总结的工具证据判断。

`LIMIT_SUMMARY_PROMPT` 让模型"基于已收集的信息给出最终回答"。当本次 run 一次工具
都没有成功调用时，"已收集的信息"是空集，模型容易用参数记忆补齐（issue #30）。这里
只判断 run 是否留下了实质可用的工具证据，供总结提示词补一句诚实下限；不替换、
不改写模型回答，也不在服务端用正则解析回答里的价格、车次、时长或气温。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence

_PRODUCT_EVIDENCE_FIELDS = {
    "place_results": ("places", ("name", "address")),
    "route_results": ("routes", ("duration_s", "distance_m", "summary", "legs")),
    "weather_results": ("forecast_days", ("high_c", "low_c", "day_weather")),
    "flight_results": ("flights", ("flight_no",)),
    "train_results": ("trains", ("train_no",)),
}


def _get_field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _has_value(value: Any) -> bool:
    """零气温/零距离也是结果，空字符串和占位容器不是。"""

    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value is not False and value != [] and value != {}


def _has_successful_source(sources: Any, *, key: str = "url") -> bool:
    return isinstance(sources, (list, tuple)) and any(
        _get_field(source, "status", "success") in {"success", "degraded"} and _has_value(_get_field(source, key))
        for source in sources
    )


def _is_usable_evidence(block: Any) -> bool:
    block_type = _get_field(block, "type")
    status = _get_field(block, "status")
    if status not in {"success", "degraded"}:
        return False
    if block_type == "knowledge_evidence":
        return _has_successful_source(_get_field(block, "source_refs"), key="evidence_id")
    fields = _PRODUCT_EVIDENCE_FIELDS.get(block_type)
    if fields is None:
        # 文件只是用户附件引用；行程视图只是产品块的引用，都不能独立证明已取得事实。
        return False
    items = _get_field(block, fields[0], [])
    return isinstance(items, (list, tuple)) and any(
        any(_has_value(_get_field(item, name)) for name in fields[1]) for item in items
    )


def _has_prefetched_page(content_blocks: list[Any] | None, evidence: RecoveryEvidenceWorkset) -> bool:
    """自动预读的 url_read 块没有 source_refs，只能按块自身的 URL 比对登记。

    登记只发生在本轮预读成功、正文已注入 messages 时（见 `record_prefetched_page`），
    因此这里不放宽"元数据不算证据"的原则，只是补上 `_eligible_blocks` 够不到的形状。
    """

    return any(
        _get_field(block, "type") == "url_read"
        and _get_field(block, "status") == "success"
        and evidence.has_source("url_read", _get_field(block, "url"))
        for block in content_blocks or []
    )


def has_tool_evidence(
    content_blocks: list[Any] | None,
    *,
    recovery_evidence: RecoveryEvidenceWorkset | None = None,
) -> bool:
    """本次 run 是否留下了成功且实质可用的来源/产品数据。"""

    # 来源卡片只持久化元数据；success、标题和 URL 均不能证明模型拿到了正文。
    # 没有运行期快照的旧调用也必须关闭这条捷径，不能从来源身份重建证据。
    if recovery_evidence is not None and (
        has_recovery_evidence(content_blocks or [], evidence=recovery_evidence)
        or _has_prefetched_page(content_blocks, recovery_evidence)
        or recovery_evidence.mcp_tool_names
    ):
        return True
    return any(_is_usable_evidence(block) for block in content_blocks or [])
