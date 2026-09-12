"""辅助功能（标题、推荐问题）共用的轻量模型配置与解析。

标题和推荐问题都是"锦上添花"的小活，固定使用轻量快速模型而不跟随对话模型，
避免对话用的是慢/贵的 thinking 模型时拖累这些小活。此前 ChatService 与
SuggestedQuestionService 各自维护了一份完全相同的常量与解析逻辑，任一处调整
都会让另一处静默失配，因此统一收敛到本模块。
"""

from __future__ import annotations

from app.ai.llm_manager import llm_manager

# 注意：qwen-max-latest 是旗舰重模型，经 LiteLLM Proxy → dashscope 实测约 20s，
# 会撞 main.py 的 TimeoutMiddleware(10s) 直接 408，故固定用快速的 deepseek-chat（实测约 3s）。
UTILITY_MODEL_ID = "deepseek-chat"

# 辅助 LLM 调用的内部超时（秒），必须 < TimeoutMiddleware 的 10s。
# 这样即便将来换的辅助模型偏慢，也能在中间件掐断前自己抛错走 fallback，而不是把 408 吐给前端。
UTILITY_LLM_TIMEOUT = 8

# 标题最终会截断到 30 字，但 deepseek-chat 会先消耗 reasoning token；
# 128 在真实回归中仍可能只返回 reasoning、正文为空，因此与推荐问题统一留足 512。
UTILITY_MAX_TOKENS = 512


def resolve_utility_model(conversation_model_id: str) -> tuple:
    """解析辅助功能模型，固定用轻量模型，找不到则回退对话模型。"""
    try:
        return llm_manager.resolve_model(UTILITY_MODEL_ID)
    except ValueError:
        return llm_manager.resolve_model(conversation_model_id)
