"""触顶总结的无证据事实边界。

`LIMIT_SUMMARY_PROMPT` 让模型"基于已收集的信息给出最终回答"。当本次 run 一次工具
都没有成功调用时，"已收集的信息"是空集，模型只能用参数记忆补齐，于是出现过 0 次工具
调用却报出具体车次时长、票价区间、自驾时长的线上案例（issue #30）。

这里只处理整个 run 没有有效工具证据的边界。冻结能力明确要求外部事实时，无论模型
使用数字、中文数字还是纯文字，都不能把查询失败改写成成功结论。动态发现路径没有
能力快照，以本轮经 tool_search 实际加载的外部工具作为同一事实需求。其余任务直接
放行，不在服务端用正则解析回答里的价格、车次、时长或气温。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.core.logger import app_logger as logger
from app.services.stream.dynamic_tool_discovery import discovery_requires_external_evidence
from app.services.stream.safe_fallback_response import default_safe_fallback
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence
from app.utils.run_capability_contract import CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES

if TYPE_CHECKING:
    from app.services.stream.run_capability_router import RunCapabilityResolution

LOG_PREFIX = "LIMIT_SUMMARY_FACT_GUARD"

# 外部事实能力包同样覆盖天气等非出行任务，文案不能只点名出行领域的班次、价格或时长
# （真实验收 Run 8139d99f：天气问题收口时出现了出行文案）。
NO_EVIDENCE_ANSWER_TEXT = default_safe_fallback("no_evidence")

# 使用已冻结的能力包，不从输出措辞重新猜测用户意图。
_EXTERNAL_FACT_PACKAGES = frozenset(
    package for package, tools in CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES.items() if tools
)
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


def _is_usable_evidence(block: Any, *, external_only: bool) -> bool:
    block_type = _get_field(block, "type")
    status = _get_field(block, "status")
    if status not in {"success", "degraded"}:
        return False
    if block_type == "knowledge_evidence":
        return not external_only and _has_successful_source(_get_field(block, "source_refs"), key="evidence_id")
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
    capability_resolution: RunCapabilityResolution | None = None,
    recovery_evidence: RecoveryEvidenceWorkset | None = None,
    tool_discovery: Any = None,
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
    external_only = requires_external_evidence(capability_resolution, tool_discovery=tool_discovery)
    return any(_is_usable_evidence(block, external_only=external_only) for block in content_blocks or [])


def requires_external_evidence(
    capability_resolution: RunCapabilityResolution | None,
    *,
    tool_discovery: Any = None,
) -> bool:
    """冻结能力或动态发现已加载的工具明确要求外部事实。目录可用性不是问候任务的证据义务。"""

    if getattr(capability_resolution, "requires_catalog_evidence", False):
        return True
    if _get_field(capability_resolution, "package_id") in _EXTERNAL_FACT_PACKAGES:
        return True
    # 显式 MCP 能力包的外部工具是授权别名，不在内置能力包工具表里。
    if _get_field(capability_resolution, "package_id") == "mcp_explicit":
        return True
    return discovery_requires_external_evidence(tool_discovery)


def resolve_no_evidence_answer(
    answer: str,
    *,
    content_blocks: list[Any] | None,
    capability_resolution: RunCapabilityResolution | None = None,
    recovery_evidence: RecoveryEvidenceWorkset | None = None,
    tool_discovery: Any = None,
) -> tuple[str, str | None]:
    """按冻结事实需求拦住无证据总结；返回 (最终答案, 触发类别)。"""

    if has_tool_evidence(
        content_blocks,
        capability_resolution=capability_resolution,
        recovery_evidence=recovery_evidence,
        tool_discovery=tool_discovery,
    ):
        return answer, None
    if requires_external_evidence(capability_resolution, tool_discovery=tool_discovery):
        return NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence"
    return answer, None


def emit_fact_guard_observation(*, fact_kind: str, summary_finish_reason: str, task_mode: str) -> None:
    """只记录固定分类，不写入模型原文或用户原文。"""

    try:
        payload = json.dumps(
            {
                "fact_kind": fact_kind,
                "summary_finish_reason": summary_finish_reason,
                "task_mode": task_mode,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        logger.info(f"{LOG_PREFIX} {payload}")
    except Exception:
        logger.warning("触顶事实边界观测日志写入失败，已忽略")
