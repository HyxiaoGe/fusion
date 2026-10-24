"""代码内置的产品配置：Agent 联网策略、模型能力展示、自动选择模型。

调整这些值走代码与发布流程；不再有数据库覆盖层。
"""

from __future__ import annotations

DEFAULT_MODEL_PRESENTATION_CONFIG = {
    "long_context_threshold_tokens": 128000,
    "weights": {
        "base": 40,
        "network": 25,
        "vision": 15,
        "long_context": 15,
        "deep_thinking": 10,
    },
    "levels": {
        "recommended": 85,
        "capable": 70,
    },
    "copy": {
        "unavailable_headline": "不建议：当前不可用",
        "default_headline": "适合：稳定知识与普通对话",
        "network_headline": "推荐：实时资料与复杂查询",
        "network_vision_headline": "推荐：实时资料和图片理解",
        "network_long_context_headline": "推荐：实时资料与长任务",
        "network_vision_long_context_headline": "推荐：实时资料、图片和长任务",
        "vision_headline": "适合：图片理解与普通对话",
        "base_reason": "可处理普通文本任务",
        "network_reason": "可联网搜索并读取关键来源",
        "vision_reason": "支持图片理解",
        "long_context_reason": "适合长上下文任务",
        "deep_thinking_reason": "适合复杂推理",
        "no_network_warning": "不支持实时联网，涉及最新信息时会基于已有知识谨慎回答",
        "network_tooltip": "可按问题需要自主联网搜索和读取关键来源",
        "no_network_tooltip": "不支持联网搜索，将基于模型知识回答",
        "vision_tooltip": "支持读图和图片理解",
        "no_vision_tooltip": "不支持图片理解",
        "deep_thinking_tooltip": "适合复杂推理和深度任务",
        "no_deep_thinking_tooltip": "不支持深度思考模式",
        "unhealthy_fallback": "服务商暂时不可用",
    },
}


# 自动选择模型的优先级：按提供商分组，组与组内模型都按顺序取第一个已注册、可调度、健康的模型，组内 flash 优先

DEFAULT_AUTO_MODEL_CONFIG = {
    "providers": [
        {"provider": "mimo", "models": ["mimo-v2.6-flash", "mimo-v2.6-pro"]},
        {"provider": "deepseek", "models": ["deepseek-chat", "deepseek-reasoner"]},
        {"provider": "qwen", "models": ["qwen3.8-flash"]},
    ],
}


DEFAULT_AGENT_STRATEGY_CONFIG = {
    "model_runtime": {
        "agent_tools_disabled_aliases": ["qwen-vl-max"],
    },
    "network": {
        "max_search_calls": 40,
        "max_url_read_calls": 100,
        "max_domains": 5,
        "min_recency_days": 1,
        "max_recency_days": 365,
    },
    "tool_context": {
        "max_context_sources": 8,
        "url_read_max_content_chars": 8000,
        "url_read_max_reason_chars": 160,
    },
}
