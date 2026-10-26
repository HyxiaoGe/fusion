"""后端产品埋点：往 PostHog 发服务端事件，和前端共用一个项目。

- distinct_id 用 Fusion user_id，和前端登录后 identify 的 ID 一致，同一用户前后端事件能连起来；
  游客前端用匿名 ID，这里按游客 user_id 记且不建人档，只能分开看。
- 只记前端看不到或看不准的结果：注册、run 终态、备用模型、额度结算与用尽、知识库入库。
  前端能直接观察的交互一律由前端记，这里不重复。
- 属性只送 ID、数量、状态、模型名、错误码；不送邮箱、昵称、消息文本、文件名。
- 没配 POSTHOG_API_KEY（本地、CI、测试）时所有调用都是空操作。
- posthog-python 自带后台线程批量发送，track 只是入队，不阻塞请求；发送失败只记日志。
  进程退出前调 shutdown() 把队列里的事件发完。
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import settings
from app.core.logger import app_logger as logger

_client: Any = None
_client_lock = threading.Lock()


def _on_error(error: Any, _batch: Any = None) -> None:
    logger.warning("product_analytics_send_failed error=%s", error)


def is_enabled() -> bool:
    return bool(settings.POSTHOG_API_KEY.strip())


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            from posthog import Posthog

            _client = Posthog(
                settings.POSTHOG_API_KEY.strip(),
                host=settings.POSTHOG_HOST.strip() or None,
                on_error=_on_error,
                # 服务端 SDK 默认用 IP 查地理位置；前端已经带了，这里不重复
                disable_geoip=True,
            )
    return _client


def track(distinct_id: str | None, event: str, properties: dict[str, Any] | None = None) -> None:
    """记一个事件；未启用或没有 distinct_id 时空操作。永不抛错，埋点失败不能影响业务。"""
    if not distinct_id or not is_enabled():
        return
    props = dict(properties or {})
    # 游客不建人档，和前端 person_profiles=identified_only 的口径一致
    if props.get("is_guest"):
        props["$process_person_profile"] = False
    try:
        _get_client().capture(event, distinct_id=str(distinct_id), properties=props)
    except Exception:  # noqa: BLE001 — 埋点只记日志
        logger.warning("product_analytics_capture_failed event=%s", event, exc_info=True)


def shutdown() -> None:
    """进程退出前把队列里的事件发完；未初始化时空操作。"""
    global _client
    client = _client
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        logger.warning("product_analytics_shutdown_failed", exc_info=True)
    _client = None


def reset_for_tests() -> None:
    global _client
    _client = None
