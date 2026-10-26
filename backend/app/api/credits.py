"""Credits 额度：当前额度、消耗流水、单条回答与整个对话的消耗；管理员查看与重置用户额度、全员重置。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user, get_current_user_or_pending_guest, get_db
from app.db.models import User
from app.schemas.response import ApiException, success
from app.services import credit_service

router = APIRouter()
admin_router = APIRouter()

_PAGE_SIZE_MAX = 100


def _entries_page(db: Session, user_id: str, limit: int, before: datetime | None) -> dict:
    entries = credit_service.list_entries(db, user_id=user_id, limit=limit, before=before)
    return {
        "items": [credit_service.serialize_entry(entry) for entry in entries],
        "next_before": credit_service.serialize_entry(entries[-1])["occurred_at"] if len(entries) == limit else None,
    }


@router.get("/me")
def get_my_credits(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_pending_guest),
):
    return success(data=credit_service.usage_summary(db, current_user), request_id=request.state.request_id)


@router.get("/me/entries")
def list_my_entries(
    request: Request,
    limit: int = Query(20, ge=1, le=_PAGE_SIZE_MAX),
    before: datetime | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_pending_guest),
):
    return success(data=_entries_page(db, str(current_user.id), limit, before), request_id=request.state.request_id)


@router.get("/messages/{message_id}")
def get_message_credits(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """一条回答各次运行的消耗；已结束但还没结算的运行在这里补结算。"""
    data = credit_service.message_credits(db, user_id=str(current_user.id), message_id=message_id)
    return success(data=data, request_id=request.state.request_id)


@router.get("/conversations/{conversation_id}")
def get_conversation_credits(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """整个对话已结算的消耗；已结束但还没结算的运行在这里补结算。"""
    data = credit_service.conversation_credits(db, user_id=str(current_user.id), conversation_id=conversation_id)
    return success(data=data, request_id=request.state.request_id)


def _target_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiException.not_found("用户不存在")
    return user


@admin_router.get("/users/{user_id}")
def get_user_credits(
    user_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=_PAGE_SIZE_MAX),
    before: datetime | None = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
):
    user = _target_user(db, user_id)
    return success(
        data={"summary": credit_service.usage_summary(db, user), **_entries_page(db, user_id, limit, before)},
        request_id=request.state.request_id,
    )


@admin_router.post("/users/{user_id}/reset")
def reset_user_credits(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    """把用户当前日、周窗口的已用量清零；之前的流水保留。"""
    user = _target_user(db, user_id)
    credit_service.reset_user_credits(db, user_id=user_id, actor_id=str(admin.id))
    return success(data=credit_service.usage_summary(db, user), request_id=request.state.request_id)


@admin_router.get("/reset-all")
def get_reset_all(
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
):
    last = credit_service.last_reset_all_at(db)
    return success(data={"last_reset_at": last.isoformat() if last else None}, request_id=request.state.request_id)


@admin_router.post("/reset-all")
def reset_all_credits(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    """把所有账号当前日、周窗口的已用量清零；之前的流水保留，日、周窗口照常按自然日和周一计算。"""
    entry = credit_service.reset_all_credits(db, actor_id=str(admin.id))
    return success(
        data={"last_reset_at": credit_service.serialize_entry(entry)["occurred_at"]},
        request_id=request.state.request_id,
    )
