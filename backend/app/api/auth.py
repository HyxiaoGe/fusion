import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_user_repo
from app.core import security
from app.core.avatar_proxy import AvatarProxyError, fetch_avatar
from app.core.guest_identity import (
    client_ip,
    guest_mode_available,
    ip_digest,
    issue_guest_token,
    reserve_new_guest_slot,
)
from app.db.database import get_db
from app.db.models import User as UserModel
from app.db.repositories import UserRepository
from app.schemas.auth import UserSettingsUpdate
from app.schemas.response import ApiException, success

logger = logging.getLogger(__name__)
router = APIRouter()

GUEST_NICKNAME = "游客"


@router.get("/avatar")
def proxy_avatar(url: str = Query(..., min_length=1)):
    """同源头像代理：按白名单抓取第三方头像并缓存，前端用同源 src 加载（绕开国内直连图床慢）。

    不鉴权——头像 URL 本就是公开的，且 ``<img>`` 请求不会带 Bearer；安全由 avatar_proxy 的
    https + host 白名单（防 SSRF）保证。``Cache-Control`` 让浏览器再缓存一天，后续秒开。
    """
    try:
        body, content_type = fetch_avatar(url)
    except AvatarProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/me")
async def read_current_user(request: Request, current_user: UserModel = Depends(security.get_current_user)):
    user_data = {
        "id": current_user.id,
        "username": current_user.username,
        "nickname": current_user.nickname,
        "avatar": current_user.avatar,
        "system_prompt": current_user.system_prompt or "",
        "is_superuser": bool(current_user.is_superuser),
        "is_guest": bool(current_user.is_guest),
    }
    return success(data=user_data, request_id=request.state.request_id)


@router.post("/guest")
def create_guest_identity(request: Request, db: Session = Depends(get_db)):
    """未登录用户领取游客身份。每次调用都新建一个游客，前端拿到令牌后长期保存复用。"""
    if not guest_mode_available():
        raise ApiException("GUEST_MODE_DISABLED", "暂未开放免登录使用，请登录后使用", status_code=403)
    ip, ip_source = client_ip(request)
    try:
        allowed = reserve_new_guest_slot(ip)
    except Exception:
        logger.warning("guest_identity_rate_limit_unavailable", exc_info=True)
        raise ApiException("GUEST_UNAVAILABLE", "暂时无法免登录使用，请稍后再试或登录", status_code=503)
    if not allowed:
        logger.info("guest_identity_rejected reason=ip_daily_limit ip=%s source=%s", ip_digest(ip), ip_source)
        raise ApiException("GUEST_LIMIT_REACHED", "今天免登录名额已用完，登录后继续使用", status_code=429)

    user_id = str(uuid.uuid4())
    user = UserRepository(db).create(
        {
            "id": user_id,
            "username": f"guest-{user_id.replace('-', '')[:16]}",
            "nickname": GUEST_NICKNAME,
            "is_guest": True,
        }
    )
    db.commit()
    db.refresh(user)
    token, expires_at = issue_guest_token(user.id)
    logger.info("guest_identity_created user=%s ip=%s source=%s", user.id, ip_digest(ip), ip_source)
    return success(
        data={
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": {
                "id": user.id,
                "username": user.username,
                "nickname": user.nickname,
                "avatar": None,
                "system_prompt": "",
                "is_superuser": False,
                "is_guest": True,
            },
        },
        request_id=request.state.request_id,
    )


@router.patch("/me")
async def update_current_user(
    body: UserSettingsUpdate,
    request: Request,
    current_user: UserModel = Depends(security.require_registered_user),
    user_repo: UserRepository = Depends(get_user_repo),
):
    """更新当前用户的个性化设置（system_prompt）"""
    updated = user_repo.update_system_prompt(current_user, body.system_prompt)
    return success(
        data={"system_prompt": updated.system_prompt},
        request_id=request.state.request_id,
    )
