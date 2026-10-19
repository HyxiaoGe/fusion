"""游客权益与额度。

登录是功能门槛而不是使用门槛：游客能聊天、能联网搜索，但只用自动选择里各提供商的
首选（flash）模型，不加载地图/生图等 MCP 工具和知识库，并受每日额度约束：

- 消息：每个游客、每个 IP、全体游客三层日额度，开始生成前预扣；回答以错误结束或没能
  开始生成时退还，用户主动停止不退。
- 并发：同一游客同时只有一个回答在生成。
- 搜索：每个游客每日次数，用完后以工具结果告知模型，由模型如实转告用户。

计数全部放在 Redis，按北京时间自然日分键；Redis 不可用时拒绝游客生成，登录用户不受影响。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.guest_identity import china_day, ip_digest
from app.core.revocation import get_redis
from app.schemas.response import ApiException
from app.services.auto_model import get_auto_provider_groups

logger = logging.getLogger(__name__)

_KEY_PREFIX = "guest_quota"
_COUNTER_TTL_SECONDS = 2 * 24 * 3600
_RELEASE_SLOT_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""

LOGIN_REQUIRED_CODE = "LOGIN_REQUIRED"
GUEST_QUOTA_EXHAUSTED_CODE = "GUEST_QUOTA_EXHAUSTED"
GUEST_RUN_IN_PROGRESS_CODE = "GUEST_RUN_IN_PROGRESS"
GUEST_UNAVAILABLE_CODE = "GUEST_UNAVAILABLE"
GUEST_SEARCH_EXHAUSTED_CODE = "guest_search_quota_exhausted"
GUEST_SEARCH_EXHAUSTED_MESSAGE = "今天免登录可用的联网搜索次数已用完，登录后可以继续联网搜索。"

# 回答以这些终态结束时退还预扣的消息额度；cancelled（用户主动停止）与 done 不退。
_REFUND_STREAM_STATUSES = frozenset({"error"})

_background_tasks: set[asyncio.Task] = set()


def is_guest(user: Any) -> bool:
    return getattr(user, "is_guest", False) is True


def guest_model_ids() -> tuple[str, ...]:
    """自动选择里每个提供商的首选模型，即各家的 flash 档。"""

    return tuple(dict.fromkeys(group.models[0] for group in get_auto_provider_groups()))


def login_required(message: str) -> ApiException:
    return ApiException(LOGIN_REQUIRED_CODE, message, status_code=403)


def _message_keys(user_id: str, ip: str, day: str) -> list[tuple[str, int]]:
    return [
        (f"{_KEY_PREFIX}:msg:{day}:{user_id}", settings.GUEST_DAILY_MESSAGES),
        (f"{_KEY_PREFIX}:msg_ip:{day}:{ip_digest(ip)}", settings.GUEST_DAILY_MESSAGES_PER_IP),
        (f"{_KEY_PREFIX}:msg_all:{day}", settings.GUEST_DAILY_MESSAGES_GLOBAL),
    ]


def _search_key(user_id: str, day: str) -> str:
    return f"{_KEY_PREFIX}:search:{day}:{user_id}"


def _slot_key(user_id: str) -> str:
    return f"{_KEY_PREFIX}:active:{user_id}"


def _unavailable() -> ApiException:
    return ApiException(GUEST_UNAVAILABLE_CODE, "暂时无法免登录使用，请稍后再试或登录", status_code=503)


@dataclass
class GuestRunReservation:
    """一次游客生成预扣的额度与并发占位；结束时恰好结算一次。"""

    user_id: str
    counter_keys: list[str]
    slot_token: str
    settled: bool = field(default=False)

    def release(self, *, refund: bool) -> None:
        if self.settled:
            return
        self.settled = True
        try:
            client = get_redis()
            if refund:
                pipe = client.pipeline()
                for key in self.counter_keys:
                    pipe.decr(key)
                pipe.execute()
            client.eval(_RELEASE_SLOT_LUA, 1, _slot_key(self.user_id), self.slot_token)
        except Exception:  # noqa: BLE001 — 结算失败只影响游客额度，占位靠 TTL 自然过期
            logger.warning("guest_quota_release_failed user=%s refund=%s", self.user_id, refund, exc_info=True)
        logger.info("guest_run_settled user=%s refund=%s", self.user_id, refund)

    def settle_after(self, task: asyncio.Task, *, conversation_id: str, task_id: str) -> None:
        """回答任务结束后按流终态结算；挂在后台，不阻塞 SSE。"""

        settle = asyncio.create_task(self._settle_when_done(task, conversation_id=conversation_id, task_id=task_id))
        _background_tasks.add(settle)
        settle.add_done_callback(_background_tasks.discard)

    async def _settle_when_done(self, task: asyncio.Task, *, conversation_id: str, task_id: str) -> None:
        from app.services.stream_state_service import get_stream_meta

        await asyncio.wait({task})
        failed = task.cancelled() or task.exception() is not None
        if not failed:
            meta = await get_stream_meta(conversation_id) or {}
            failed = meta.get("task_id") == task_id and meta.get("status") in _REFUND_STREAM_STATUSES
        await asyncio.to_thread(self.release, refund=failed)


def reserve_guest_run(user_id: str, ip: str, *, now: float | None = None) -> GuestRunReservation:
    """游客开始一次生成前占并发位并预扣三层消息额度；不满足时抛出可展示给用户的错误。"""

    day = china_day(now)
    keys = _message_keys(user_id, ip, day)
    slot_token = uuid.uuid4().hex
    try:
        client = get_redis()
        if not client.set(_slot_key(user_id), slot_token, nx=True, ex=settings.GUEST_RUN_SLOT_TTL_SECONDS):
            raise ApiException(GUEST_RUN_IN_PROGRESS_CODE, "上一条回答还在生成，请稍候再发送", status_code=429)
        pipe = client.pipeline()
        for key, _ in keys:
            pipe.incr(key)
            pipe.expire(key, _COUNTER_TTL_SECONDS)
        counts = pipe.execute()[::2]
    except ApiException:
        raise
    except Exception:
        logger.warning("guest_quota_unavailable user=%s", user_id, exc_info=True)
        raise _unavailable() from None

    reservation = GuestRunReservation(user_id=user_id, counter_keys=[key for key, _ in keys], slot_token=slot_token)
    exceeded = [key for (key, limit), count in zip(keys, counts) if count > limit]
    if exceeded:
        reservation.release(refund=True)
        scope = exceeded[0].split(":")[1]
        logger.info("guest_quota_rejected user=%s scope=%s", user_id, scope)
        message = (
            "今天免登录可发送的消息已用完，登录后继续使用" if scope == "msg" else "今天免登录名额已用完，登录后继续使用"
        )
        raise ApiException(GUEST_QUOTA_EXHAUSTED_CODE, message, status_code=429)
    used_today, ip_used_today, all_used_today = counts
    logger.info(
        "guest_run_reserved user=%s used=%s ip_used=%s all_used=%s",
        user_id,
        used_today,
        ip_used_today,
        all_used_today,
    )
    return reservation


def reserve_guest_search(user_id: str, *, now: float | None = None) -> bool:
    """预扣一次游客搜索；超出日额度返回 False（多扣的一次不退，额度当天已用尽）。"""

    key = _search_key(user_id, china_day(now))
    client = get_redis()
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, _COUNTER_TTL_SECONDS)
    count = pipe.execute()[0]
    allowed = count <= settings.GUEST_DAILY_SEARCHES
    logger.info("guest_search_reserved user=%s used=%s allowed=%s", user_id, count, allowed)
    return allowed


def guest_usage(user_id: str, *, now: float | None = None) -> dict[str, dict[str, int]] | None:
    """游客今日已用与剩余额度；Redis 不可用时返回 None，由前端不展示计数。"""

    day = china_day(now)
    try:
        used_messages, used_searches = get_redis().mget(
            f"{_KEY_PREFIX}:msg:{day}:{user_id}",
            _search_key(user_id, day),
        )
    except Exception:  # noqa: BLE001
        logger.warning("guest_usage_unavailable user=%s", user_id, exc_info=True)
        return None

    def _usage(used: Any, limit: int) -> dict[str, int]:
        used_count = min(int(used or 0), limit)
        return {"limit": limit, "used": used_count, "remaining": limit - used_count}

    return {
        "messages": _usage(used_messages, settings.GUEST_DAILY_MESSAGES),
        "searches": _usage(used_searches, settings.GUEST_DAILY_SEARCHES),
    }


def build_entitlements(user: Any) -> dict[str, Any]:
    """前端按此决定哪些入口可用、哪些显示「登录后可用」。"""

    guest = is_guest(user)
    return {
        "tier": "guest" if guest else "registered",
        "features": {
            "model_selection": not guest,
            "file_upload": not guest,
            "image_generation": not guest,
            "map": not guest,
            "knowledge_base": not guest,
            "mcp_tools": not guest,
            "personalization": not guest,
        },
        "models": list(guest_model_ids()) if guest else None,
        "quota": guest_usage(str(user.id)) if guest else None,
    }
