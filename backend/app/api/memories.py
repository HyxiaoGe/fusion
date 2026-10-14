"""用户记忆：查看、删除、清空、开关。条目由后台整理任务写入，这里不提供新增或编辑。"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_registered_user
from app.db.models import User
from app.schemas.response import ApiException, ApiResponse, success
from app.schemas.user_memory import UserMemoryOverview, UserMemorySettingsUpdate
from app.services.user_memory_service import UserMemoryService

router = APIRouter()


@router.get("", response_model=ApiResponse[UserMemoryOverview])
def get_memories(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_registered_user),
):
    items = UserMemoryService(db).list_items(current_user.id)
    data = UserMemoryOverview(enabled=bool(current_user.memory_enabled), items=[asdict(item) for item in items])
    return success(data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.put("/settings")
def update_memory_settings(
    payload: UserMemorySettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_registered_user),
):
    enabled = UserMemoryService(db).set_enabled(current_user.id, payload.enabled)
    return success(data={"enabled": enabled}, request_id=request.state.request_id)


@router.delete("/{memory_id}")
def delete_memory(
    request: Request,
    memory_id: str = Path(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_registered_user),
):
    if not UserMemoryService(db).delete_item(current_user.id, memory_id):
        raise ApiException("MEMORY_NOT_FOUND", "这条记忆不存在或已删除", status_code=404)
    return success(data={"deleted": True}, request_id=request.state.request_id)


@router.delete("")
def clear_memories(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_registered_user),
):
    deleted = UserMemoryService(db).clear(current_user.id)
    return success(data={"deleted": deleted}, request_id=request.state.request_id)
