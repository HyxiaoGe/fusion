"""触顶总结的无证据事实边界。

`LIMIT_SUMMARY_PROMPT` 让模型"基于已收集的信息给出最终回答"。当本次 run 一次工具
都没有成功调用时，"已收集的信息"是空集，模型只能用参数记忆补齐，于是出现过 0 次工具
调用却报出具体车次时长、票价区间、自驾时长的线上案例（issue #30）。

`validate_product_answer()` 拦不住这种情况：它以产品结果块为事实底表，没有结果块时
直接返回 `missing_product_result`，而触顶路径根本没有接入它。

这里只处理整个 run 没有有效工具证据的边界。冻结能力明确要求外部事实时，无论模型
使用数字、中文数字还是纯文字，都不能把查询失败改写成成功结论。普通改写和稳定知识
任务不受该边界约束；没有能力快照的旧调用仍保留动态数值守卫。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.core.logger import app_logger as logger
from app.services.stream.safe_fallback_response import default_safe_fallback
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence
from app.utils.run_capability_contract import CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES

if TYPE_CHECKING:
    from app.services.stream.run_capability_router import RunCapabilityResolution

LOG_PREFIX = "LIMIT_SUMMARY_FACT_GUARD"

# 本模块同样拦截气温等非出行数值，文案不能只点名出行领域的班次、价格或时长
# （真实验收 Run 8139d99f：天气问题收口时出现了出行文案）。
NO_EVIDENCE_ANSWER_TEXT = default_safe_fallback("no_evidence")

# 使用已冻结的能力包，不从输出措辞重新猜测用户意图。
_EXTERNAL_FACT_PACKAGES = frozenset(
    package for package, tools in CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES.items() if tools
)
_CONTEXT_ONLY_PACKAGES = frozenset({"direct", "transform", "date"})
_PRODUCT_EVIDENCE_FIELDS = {
    "place_results": ("places", ("name", "address")),
    "route_results": ("routes", ("duration_s", "distance_m", "summary", "legs")),
    "weather_results": ("forecast_days", ("high_c", "low_c", "day_weather")),
    "flight_results": ("flights", ("flight_no",)),
    "train_results": ("trains", ("train_no",)),
}

# 只收具体到可被用户当作查询结果的动态数值，不收泛化建议里的数字。
_DYNAMIC_FACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 票价/金额：300元、¥300、300块、300 元左右
    ("price", re.compile(r"(?:[¥￥]\s*\d+(?:\.\d+)?)|(?:\d+(?:\.\d+)?\s*(?:元|块|人民币|rmb))", re.IGNORECASE)),
    # 车次/航班号：G1234、D23、CZ3456、MU5678
    # 车次字母是铁路封闭集合，航司二字码固定 3-4 位数字，不做开放式字母匹配。
    # G/S 同时是国家高速与省道编号（G4 高速），排在道路后缀前的不算车次。
    (
        "vehicle_number",
        re.compile(r"\b(?:[GDCZTKYSLP]\d{1,4}|[A-Z]{2}\d{3,4})\b(?!\s*(?:高速|国道|省道|公路|线|路))"),
    ),
    # 行程时长：5小时、4.5 小时、50分钟、3个半小时
    ("duration", re.compile(r"\d+(?:\.\d+)?\s*(?:个)?(?:半)?\s*(?:小时|分钟|h\b|min\b)", re.IGNORECASE)),
    # 气温：18度、18℃、-2°C；天气工具不可用时同样会被参数记忆补齐。
    ("temperature", re.compile(r"-?\d+(?:\.\d+)?\s*(?:度|℃|°C)", re.IGNORECASE)),
)

# 去掉数字与单位后用于比对"是否用户自己说过"的归一化。
_NORMALIZE_RE = re.compile(r"[\s,，]")


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
) -> bool:
    """本次 run 是否留下了成功且实质可用的来源/产品数据。"""

    # 来源卡片只持久化元数据；success、标题和 URL 均不能证明模型拿到了正文。
    # 没有运行期快照的旧调用也必须关闭这条捷径，不能从来源身份重建证据。
    if recovery_evidence is not None and (
        has_recovery_evidence(content_blocks or [], evidence=recovery_evidence)
        or _has_prefetched_page(content_blocks, recovery_evidence)
    ):
        return True
    external_only = requires_external_evidence(capability_resolution)
    return any(_is_usable_evidence(block, external_only=external_only) for block in content_blocks or [])


def requires_external_evidence(capability_resolution: RunCapabilityResolution | None) -> bool:
    """只使用冻结能力中明确需要查询外部事实的包。"""

    return _get_field(capability_resolution, "package_id") in _EXTERNAL_FACT_PACKAGES


def _conversation_text(messages: list[dict] | None) -> str:
    """只取用户输入；模型自己上一轮说过的话不能作为事实支撑。"""

    parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(item.get("text", "") for item in content if isinstance(item, dict))
    return _NORMALIZE_RE.sub("", "\n".join(parts)).lower()


def unsupported_dynamic_fact_kind(answer: str, *, messages: list[dict] | None = None) -> str | None:
    """返回首个无上下文支撑的动态事实类别；没有则返回 None。

    用户自己说过的数值（"我十点要到机场"）算上下文支撑，不计入。
    """

    if not isinstance(answer, str) or not answer.strip():
        return None
    supported_text = _conversation_text(messages)
    for kind, pattern in _DYNAMIC_FACT_PATTERNS:
        for match in pattern.finditer(answer):
            if _NORMALIZE_RE.sub("", match.group(0)).lower() not in supported_text:
                return kind
    return None


def resolve_no_evidence_answer(
    answer: str,
    *,
    content_blocks: list[Any] | None,
    messages: list[dict] | None = None,
    capability_resolution: RunCapabilityResolution | None = None,
    recovery_evidence: RecoveryEvidenceWorkset | None = None,
) -> tuple[str, str | None]:
    """按冻结事实需求拦住无证据总结；返回 (最终答案, 触发类别)。"""

    if has_tool_evidence(
        content_blocks, capability_resolution=capability_resolution, recovery_evidence=recovery_evidence
    ):
        return answer, None
    package_id = _get_field(capability_resolution, "package_id")
    if requires_external_evidence(capability_resolution):
        return NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence"
    if package_id in _CONTEXT_ONLY_PACKAGES:
        return answer, None
    kind = unsupported_dynamic_fact_kind(answer, messages=messages)
    if kind is None:
        return answer, None
    return NO_EVIDENCE_ANSWER_TEXT, kind


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
