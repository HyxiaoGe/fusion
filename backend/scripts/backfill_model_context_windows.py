"""一次性脚本：给 LiteLLM DB 模型补 `model_info.max_input_tokens`（上下文窗口）。

缺窗口的模型在 chat 链路里走 `bypass_unknown_window`，上下文管理整个不生效。
这里只补有正式来源的值（厂商模型页 / LiteLLM 成本表里同一上游的条目），
没有来源的模型保持未知，不推测。已经有窗口的模型一律不动。

写入走 `PATCH /model/{id}/update`：只合并 model_info 的这一个字段，
不回写 litellm_params，避免把 /model/info 里不返回的 api_key 冲掉。

用法（默认 dry-run，APPLY=1 才写）：
    LITELLM_BASE_URL=http://localhost:4000 \
    LITELLM_MASTER_KEY=sk-litellm-master-... \
    APPLY=1 python scripts/backfill_model_context_windows.py
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

# 上游模型（litellm_params.model）→ (窗口 tokens, 来源)。按上游而不是别名匹配，
# 别名改名或多个别名指向同一上游时不会漏补/错补。
CONTEXT_WINDOWS: dict[str, tuple[int, str]] = {
    "openai/mimo-v2.5-pro": (1048576, "https://mimo.mi.com/models/zh-CN/mimo-v2.5-pro（上下文长度 1M）"),
    "openai/mimo-v2.6-pro": (1048576, "https://mimo.mi.com/models/zh-CN/mimo-v2.6-pro（上下文长度 1M）"),
    "openai/mimo-v2.6-flash": (1048576, "https://mimo.mi.com/models/zh-CN/mimo-v2.6-flash（上下文长度 1M）"),
    "minimax/MiniMax-M2.7": (
        204800,
        "https://platform.minimax.cn/docs/guides/text-generation（上下文窗口 204,800，含输出）",
    ),
    "openai/qwen3.8-max": (991808, "LiteLLM 成本表 dashscope/qwen3.8-max"),
    "openai/qwen3.7-max": (991808, "LiteLLM 成本表 dashscope/qwen3.7-max"),
    "openai/qwen3.6-plus": (1000000, "LiteLLM 成本表 openrouter/qwen/qwen3.6-plus（百炼未单列）"),
    "openai/doubao-seed-2-0-pro-260215": (256000, "LiteLLM 成本表 volcengine/doubao-seed-2-0-pro-260215"),
    "openai/doubao-seed-2-0-lite-260215": (256000, "LiteLLM 成本表 volcengine/doubao-seed-2-0-lite-260215"),
    "openai/doubao-seed-2-0-mini-260428": (
        256000,
        "LiteLLM 成本表 volcengine/doubao-seed-2-0-mini-260215（同系列 256K）",
    ),
}


def _existing_window(info: dict[str, Any]) -> int | None:
    value = info.get("max_input_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def plan_updates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只挑 DB 模型里缺窗口且有来源的条目；同一 model_id 只出现一次。"""
    planned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        info = entry.get("model_info") or {}
        model_id = info.get("id")
        if not info.get("db_model") or not model_id or model_id in seen:
            continue
        if _existing_window(info) is not None:
            continue
        underlying = (entry.get("litellm_params") or {}).get("model") or ""
        window = CONTEXT_WINDOWS.get(underlying)
        if window is None:
            continue
        seen.add(model_id)
        planned.append(
            {
                "model_id": model_id,
                "model_name": entry.get("model_name"),
                "underlying": underlying,
                "max_input_tokens": window[0],
                "source": window[1],
            }
        )
    return planned


def main() -> int:
    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}"}
    apply = os.environ.get("APPLY") == "1"

    with httpx.Client(timeout=15.0, headers=headers) as client:
        resp = client.get(f"{base_url}/model/info")
        resp.raise_for_status()
        planned = plan_updates(resp.json().get("data", []))

        failed = 0
        for item in planned:
            label = f"{item['model_name']} ({item['underlying']}) → {item['max_input_tokens']}  [{item['source']}]"
            if not apply:
                print(f"  [dry-run] {label}")
                continue
            update_resp = client.patch(
                f"{base_url}/model/{item['model_id']}/update",
                json={"model_info": {"max_input_tokens": item["max_input_tokens"]}},
            )
            if update_resp.status_code in (200, 201):
                print(f"  [ok] {label}")
            else:
                print(f"  [FAIL] {label}: HTTP {update_resp.status_code} {update_resp.text[:200]}")
                failed += 1

    print()
    print(f"{'写入' if apply else '计划'} {len(planned) - failed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
