"""
按用户登记的对话活动：哪些对话正在生成。

运行本身独立于页面连接（后台任务写 Redis Stream），用户可以离开对话或关掉页面。
侧栏需要跨标签页、跨刷新知道哪些对话在跑，因此在 Redis 里按用户记一个进行中集合：
流初始化时加入，结束或取消时移除；读取时再按流 meta 校验，进程崩溃等漏移除的条目
会在读取时被清掉。完成未读由通知中心管理，不在这里。

全部尽力而为：Redis 出错只记日志，不影响生成本身。
"""

from app.core.logger import app_logger as logger
from app.core.redis import (
    STREAM_CHUNK_TTL,
    get_redis_pool,
    stream_meta_key,
    user_active_streams_key,
)


async def mark_stream_started(user_id: str, conversation_id: str) -> None:
    redis = get_redis_pool()
    if not redis or not user_id:
        return
    try:
        active_key = user_active_streams_key(user_id)
        pipe = redis.pipeline(transaction=False)
        pipe.sadd(active_key, conversation_id)
        pipe.expire(active_key, STREAM_CHUNK_TTL)
        await pipe.execute()
    except Exception as error:
        logger.warning("登记进行中对话失败: conv_id=%s, error=%s", conversation_id, error)


async def mark_stream_finished(conversation_id: str) -> None:
    redis = get_redis_pool()
    if not redis:
        return
    try:
        # 结束后 meta 仍保留 STREAM_DONE_TTL，可从中取回归属用户。
        user_id = await redis.hget(stream_meta_key(conversation_id), "user_id")
        if not user_id:
            return
        await redis.srem(user_active_streams_key(user_id), conversation_id)
    except Exception as error:
        logger.warning("登记对话结束失败: conv_id=%s, error=%s", conversation_id, error)


async def get_conversation_activity(user_id: str) -> dict[str, list[str]]:
    redis = get_redis_pool()
    if not redis:
        return {"streaming": []}
    active_key = user_active_streams_key(user_id)
    active_ids = sorted(await redis.smembers(active_key))

    streaming: list[str] = []
    stale: list[str] = []
    if active_ids:
        pipe = redis.pipeline(transaction=False)
        for conversation_id in active_ids:
            pipe.hmget(stream_meta_key(conversation_id), "status", "user_id")
        for conversation_id, (status, owner) in zip(active_ids, await pipe.execute()):
            if status == "streaming" and owner == user_id:
                streaming.append(conversation_id)
            else:
                stale.append(conversation_id)
    if stale:
        await redis.srem(active_key, *stale)
    return {"streaming": streaming}
