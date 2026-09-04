"""触顶总结的无证据事实边界。

`LIMIT_SUMMARY_PROMPT` 让模型"基于已收集的信息给出最终回答"。当本次 run 一次工具
都没有成功调用时，"已收集的信息"是空集，模型只能用参数记忆补齐，于是出现过 0 次工具
调用却报出具体车次时长、票价区间、自驾时长的线上案例（issue #30）。

`validate_product_answer()` 拦不住这种情况：它以产品结果块为事实底表，没有结果块时
直接返回 `missing_product_result`，而触顶路径根本没有接入它。

这里只处理边界最干净的那一类：**整个 run 没有任何工具证据**。此时任何未出现在对话
输入里的具体动态数值都无从支撑，不需要做模糊的事实比对。有任何证据块时本模块一律
放行，交给既有的证据校验链路。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import app_logger as logger

LOG_PREFIX = "LIMIT_SUMMARY_FACT_GUARD"

NO_EVIDENCE_ANSWER_TEXT = "本次没能完成所需的查询，暂时无法给出可靠的班次、价格或时长。你可以稍后重试。"

# 工具证据块：搜索、网页读取、知识证据与全部产品结果块。
_EVIDENCE_BLOCK_TYPES = frozenset(
    {
        "search",
        "url_read",
        "knowledge_evidence",
        "file",
        "place_results",
        "route_results",
        "weather_results",
        "flight_results",
        "train_results",
        "itinerary_results",
    }
)

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


def has_tool_evidence(content_blocks: list[Any] | None) -> bool:
    """本次 run 是否留下了任何工具证据块。"""

    for block in content_blocks or []:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type in _EVIDENCE_BLOCK_TYPES:
            return True
    return False


def _conversation_text(messages: list[dict] | None) -> str:
    """只取用户输入；模型自己上一轮说过的话不能作为事实支撑。"""

    parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
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
) -> tuple[str, str | None]:
    """无证据时拦下具体动态数值；返回 (最终答案, 触发类别)。"""

    if has_tool_evidence(content_blocks):
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
