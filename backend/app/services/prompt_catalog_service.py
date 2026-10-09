"""面向 UI 的提示词模板目录。"""

from __future__ import annotations

import copy
from typing import Any

from app.services.config_defaults import DEFAULT_HOME_PROMPT_CATALOG


def get_home_prompt_catalog() -> dict[str, Any]:
    """首页任务卡和系统模板，来自代码内置目录。"""

    items = [
        copy.deepcopy(item)
        for item in DEFAULT_HOME_PROMPT_CATALOG.get("items", [])
        if isinstance(item, dict) and item.get("enabled") is True
    ]
    items.sort(key=lambda item: (item.get("sort_order", 0), item.get("id", "")))
    return {
        "items": items,
        "source": "default",
        "version": "code-default",
    }
