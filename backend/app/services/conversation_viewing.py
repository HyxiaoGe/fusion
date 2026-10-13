"""用户正在查看的对话：前台对话页定时上报，回答结束时据此决定是否需要站内通知。"""

from app.core.logger import app_logger as logger
from app.core.revocation import get_redis

# 前端每 15 秒上报一次；标记过期前允许漏掉一次上报。
VIEWING_TTL_SECONDS = 30


def _viewing_key(user_id: str, conversation_id: str) -> str:
    return f"conversation:viewing:{user_id}:{conversation_id}"


def mark_viewing(user_id: str, conversation_id: str) -> None:
    get_redis().set(_viewing_key(user_id, conversation_id), "1", ex=VIEWING_TTL_SECONDS)


def clear_viewing(user_id: str, conversation_id: str) -> None:
    get_redis().delete(_viewing_key(user_id, conversation_id))


def is_viewing(user_id: str, conversation_id: str) -> bool:
    """读不到标记时按没人在看处理，宁可多一条通知也不能漏掉。"""
    try:
        return bool(get_redis().exists(_viewing_key(user_id, conversation_id)))
    except Exception as error:  # noqa: BLE001 — Redis 不可用时照常建通知
        logger.warning("读取对话查看状态失败，照常建通知: error_type=%s", type(error).__name__)
        return False
