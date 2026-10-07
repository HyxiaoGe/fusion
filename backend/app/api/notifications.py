"""当前登录用户的通知中心接口。"""

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_current_user, get_notification_service
from app.db.models import User
from app.schemas.notification import (
    NotificationFilter,
    NotificationPage,
    NotificationReadAllRequest,
    NotificationReadRequest,
    NotificationReadResult,
    NotificationReadResultRequest,
)
from app.schemas.response import ApiResponse, success
from app.services.notification_service import NotificationService

router = APIRouter()


@router.get("", response_model=ApiResponse[NotificationPage])
def list_notifications(
    request: Request,
    filter: NotificationFilter = Query("all"),
    cursor: str | None = Query(None, max_length=20),
    limit: int = Query(20, ge=1, le=100),
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
):
    page = service.list_notifications(str(current_user.id), filter=filter, cursor=cursor, limit=limit)
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
