"""登录用户读取更新日志，管理员通过发布接口创建不可变版本。"""

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_changelog_service, get_current_admin_user, get_current_user
from app.db.models import User
from app.schemas.changelog import ChangelogDetail, ChangelogPage, ChangelogPublishRequest
from app.schemas.response import ApiResponse, success
from app.services.changelog_service import ChangelogService

router = APIRouter()
admin_router = APIRouter()


@admin_router.post("", response_model=ApiResponse[ChangelogDetail])
def publish_changelog(
    payload: ChangelogPublishRequest,
    request: Request,
    service: ChangelogService = Depends(get_changelog_service),
    admin: User = Depends(get_current_admin_user),
):
    result = service.publish(str(admin.id), payload)
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)


@router.get("", response_model=ApiResponse[ChangelogPage])
def list_changelogs(
    request: Request,
    cursor: str | None = Query(None, max_length=128),
    limit: int = Query(20, ge=1, le=100),
    service: ChangelogService = Depends(get_changelog_service),
    user: User = Depends(get_current_user),
):
    result = service.list_changelogs(cursor=cursor, limit=limit)
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)


@router.get("/{changelog_id}", response_model=ApiResponse[ChangelogDetail])
def get_changelog(
    changelog_id: str,
    request: Request,
    service: ChangelogService = Depends(get_changelog_service),
    user: User = Depends(get_current_user),
):
    result = service.get_changelog(str(user.id), changelog_id)
    return success(data=result.model_dump(mode="json"), request_id=request.state.request_id)
