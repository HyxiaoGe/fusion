from typing import Any, Mapping

APP_TAG = "app:fusion"
PROXY_NUM_RETRIES_HEADER = "x-litellm-num-retries"

ALLOWED_LLM_PHASES = frozenset(
    {
        "chat_non_stream",
        "chat_stream",
        "generate_title",
        "suggest_questions",
        "file_processing",
        "fallback_language",
        "eval_judge",
    }
)


def build_litellm_metadata(phase: str) -> dict[str, list[str]]:
    """构造 LiteLLM SpendLogs 可消费的低基数业务标签。"""
    if phase not in ALLOWED_LLM_PHASES:
        raise ValueError(f"未知 LLM phase: {phase}")
    return {"tags": [APP_TAG, f"phase:{phase}"]}


def merge_litellm_kwargs(
    phase: str,
    kwargs: Mapping[str, Any] | None = None,
    *,
    prompt_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(kwargs or {})
    merged.pop("metadata", None)
    merged["extra_body"] = merge_openai_extra_body(
        phase,
        merged.get("extra_body"),
        prompt_metadata=prompt_metadata,
    )
    # 重试只由调用方负责。SDK（litellm + openai 客户端）和代理默认各自重试 2 次，层层相乘：
    # 一次失败最多放大到 9 次上游请求，并吃掉分类器/辅助调用的时间预算。
    merged["num_retries"] = 0
    merged["max_retries"] = 0
    merged["extra_headers"] = {**(merged.get("extra_headers") or {}), PROXY_NUM_RETRIES_HEADER: "0"}
    return merged


def merge_openai_extra_body(
    phase: str,
    extra_body: Mapping[str, Any] | None = None,
    *,
    prompt_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(extra_body or {})
    metadata = dict(merged.get("metadata") or {})
    metadata["tags"] = build_litellm_metadata(phase)["tags"]
    for key in ("prompt_slug", "prompt_version", "prompt_revision", "source_kind", "effective_revision"):
        value = (prompt_metadata or {}).get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    merged["metadata"] = metadata
    # 代理开着响应缓存：同一 prompt 会原样复用上次输出，一次坏的分类结果能连续命中数分钟。
    merged["cache"] = {"no-cache": True, "no-store": True}
    return merged
