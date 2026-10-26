"""当前登录用户的通知中心接口。"""

from fastapi import APIRouter, Depends, Path, Query, Request

from app.api.deps import get_current_user, get_current_user_or_pending_guest, get_notification_service
from app.db.models import User
from app.schemas.notification import (
    NotificationBusinessType,
    NotificationFilter,
    NotificationPage,
    NotificationReadAllRequest,
    NotificationReadRequest,
    NotificationReadResult,
    NotificationReadResultRequest,
)
from app.schemas.response import ApiResponse, success
from app.services.conversation_viewing import clear_viewing
from app.services.notification_service import NotificationService

router = APIRouter()


@router.get("", response_model=ApiResponse[NotificationPage])
def list_notifications(
    request: Request,
    filter: NotificationFilter = Query("all"),
    business_type: NotificationBusinessType | None = Query(None),
    cursor: str | None = Query(None, max_length=20),
    limit: int = Query(20, ge=1, le=100),
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user_or_pending_guest),
):
    page = service.list_notifications(
        str(current_user.id), filter=filter, business_type=business_type, cursor=cursor, limit=limit
    )
    return success(data=page.model_dump(mode="json"), request_id=request.state.request_id)


@router.post("/read", response_model=ApiResponse[NotificationReadResult])
def mark_notifications_read(
    payload: NotificationReadRequest,
    request: Request,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
):
    result = service.mark_read(str(current_user.id), payload.ids)
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)


@router.post("/read-all", response_model=ApiResponse[NotificationReadResult])
def mark_all_notifications_read(
    payload: NotificationReadAllRequest,
    request: Request,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
):
    result = service.mark_all_read(str(current_user.id), payload.through_revision)
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)


@router.post("/read-result", response_model=ApiResponse[NotificationReadResult])
def mark_notification_result_read(
    payload: NotificationReadResultRequest,
    request: Request,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
):
    result = service.mark_result_read(str(current_user.id), **payload.model_dump())
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)


# 「正在看」标记由 /api/sync 续期；页面转入后台或离开对话时立即撤销，只删当前用户自己的键。
_CONVERSATION_ID = Path(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.delete("/viewing/{conversation_id}", response_model=ApiResponse[None])
def clear_conversation_viewing(
    request: Request,
    conversation_id: str = _CONVERSATION_ID,
    current_user: User = Depends(get_current_user),
):
    clear_viewing(str(current_user.id), conversation_id)
    return success(data=None, request_id=request.state.request_id)
