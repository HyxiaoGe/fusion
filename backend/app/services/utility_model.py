"""辅助功能（标题、推荐问题）共用的轻量模型配置与解析。

标题和推荐问题都是"锦上添花"的小活，固定使用轻量快速模型而不跟随对话模型，
避免对话用的是慢/贵的 thinking 模型时拖累这些小活。此前 ChatService 与
SuggestedQuestionService 各自维护了一份完全相同的常量与解析逻辑，任一处调整
都会让另一处静默失配，因此统一收敛到本模块。
"""

from __future__ import annotations

from app.ai.litellm_utils import merge_extra_body
from app.ai.llm_manager import llm_manager

# 注意：qwen-max-latest 是旗舰重模型，经 LiteLLM Proxy → dashscope 实测约 20s，
# 会撞 main.py 的 TimeoutMiddleware(10s) 直接 408，故固定用快速的 deepseek-chat（实测约 3s）。
UTILITY_MODEL_ID = "deepseek-chat"

# 辅助 LLM 调用的内部超时（秒），必须 < TimeoutMiddleware 的 10s。
# 这样即便将来换的辅助模型偏慢，也能在中间件掐断前自己抛错走 fallback，而不是把 408 吐给前端。
UTILITY_LLM_TIMEOUT = 8

# 标题最终会截断到 30 字，推荐问题也只要三行，512 是留给正文的宽裕余量。
# 注意这份预算同时覆盖 reasoning 与正文：deepseek-chat 会先产 reasoning token，
# 曾经 128 被吃光后只返回 reasoning、正文为空（finish_reason=length、raw_chars=0），
# 当时的处置是抬到 512，线上又复现了一次。抬预算治不了根，而且越大越容易撞
# UTILITY_LLM_TIMEOUT，所以现在由 resolve_utility_model 统一关掉推理。
UTILITY_MAX_TOKENS = 512


def resolve_utility_model(conversation_model_id: str) -> tuple:
    """解析辅助功能模型，固定用轻量模型，找不到则回退对话模型。

    返回的 kwargs 一律带上禁用推理：标题、推荐问题、安全兜底语言选择都是小活，
    不需要推理，而 reasoning token 与正文共用 UTILITY_MAX_TOKENS，一旦被吃光
    就只剩空正文。回退分支更需要——那时用的正是可能很重的会话模型。
    在这里统一处理而不是在三个调用点各写一遍，理由同本模块开头：分散维护必然失配。
    """
    try:
        model, provider, kwargs = llm_manager.resolve_model(UTILITY_MODEL_ID)
    except ValueError:
        model, provider, kwargs = llm_manager.resolve_model(conversation_model_id)
    # llm_manager 可能返回共享的配置对象，就地改会污染其他调用方，必须复制后再合并。
    kwargs = dict(kwargs or {})
    merge_extra_body(kwargs, {"thinking": {"type": "disabled"}})
    return model, provider, kwargs
