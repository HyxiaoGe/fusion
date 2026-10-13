"""前端后台节拍的聚合同步接口：把同一节拍的心跳、活动状态、通知版本合成一个请求。"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_notification_service
from app.core.logger import app_logger as logger
from app.db.models import User
from app.schemas.response import ApiResponse, success
from app.schemas.sync import SyncActivity, SyncNotifications, SyncRequest, SyncResponse
from app.services.conversation_activity_service import get_conversation_activity
from app.services.conversation_viewing import mark_viewing
from app.services.notification_service import NotificationService

router = APIRouter()


@router.post("", response_model=ApiResponse[SyncResponse])
async def sync(
    payload: SyncRequest,
    request: Request,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    result = SyncResponse()

    if payload.viewing_conversation_id is not None:
        try:
            await run_in_threadpool(mark_viewing, user_id, payload.viewing_conversation_id)
            result.viewing = True
        except Exception as error:  # noqa: BLE001 — 单部分失败只影响该部分
            logger.warning("同步续期查看标记失败: user_id=%s, error_type=%s", user_id, type(error).__name__)
            result.failed.append("viewing")

    if payload.activity:
        try:
            result.activity = SyncActivity(**await get_conversation_activity(user_id))
        except Exception as error:  # noqa: BLE001
            logger.warning("同步读取对话活动状态失败: user_id=%s, error_type=%s", user_id, type(error).__name__)
            result.failed.append("activity")

    if payload.notifications:
        try:
            result.notifications = SyncNotifications(
                revision=await run_in_threadpool(service.current_revision, user_id)
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("同步读取通知版本失败: user_id=%s, error_type=%s", user_id, type(error).__name__)
            result.failed.append("notifications")

    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)
