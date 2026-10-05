"""地名歧义：工具因同名地点有多个候选而停下，等待模型按候选重查或请用户确认。

这是设计内的确认流程，不是工具故障；结果对象仍保持 failed（模型据此处理候选），
只有对外展示的事件状态按“待确认”呈现。
"""

from __future__ import annotations

from typing import Any

AMBIGUOUS_LOCATION_ERROR_CODE = "ambiguous_location"
PLACE_CHOICE_REPAIR_STATE = "awaiting_choice"


def pending_place_choice(status: Any, data: Any) -> dict[str, Any] | None:
    """地名歧义失败且带候选时返回候选详情；修参通道（如天气）不走这里。"""

    if status != "failed" or not isinstance(data, dict) or "repair" in data:
        return None
    details = data.get("error_details")
    if data.get("error_code") != AMBIGUOUS_LOCATION_ERROR_CODE or not isinstance(details, dict):
        return None
    candidates = details.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    return details
