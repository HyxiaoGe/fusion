"""会话里画的图：图存在 image-service 里，Fusion 后端按编号经网关取来转给页面（不暴露服务地址）。

- GET /{image_id}/url：登录用户取自己会话里的图的地址（带短期签名 token）。
- GET /{image_id}/content?token=…：页面用 <img src> 取图，凭签名 token；图所在的会话删了就取不到。
  内容不会变，浏览器可以一直缓存。网关、服务出问题回 502，页面显示加载失败、可以重试。
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.logger import app_logger as logger
from app.db.models import User
from app.schemas.response import ApiException, ErrorCode, success
from app.services.external.agent_tools_images import ImageStoreError, fetch_image
from app.services.generated_images import image_exists, signed_image_url, user_image, verify_image_token

router = APIRouter()


@router.get("/{image_id}/url")
def get_generated_image_url(
    image_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if user_image(db, image_id=image_id, user_id=str(current_user.id)) is None:
        raise ApiException.not_found("图片不存在或无权访问")
    return success(data={"url": signed_image_url(image_id)}, request_id=request.state.request_id)


@router.get("/{image_id}/content")
async def get_generated_image_content(
    image_id: str,
    token: str = Query(..., max_length=512),
    db: Session = Depends(get_db),
):
    if not verify_image_token(token, image_id):
        raise ApiException.unauthorized("无效或过期的图片访问令牌")
    if not image_exists(db, image_id):
        raise ApiException.not_found("图片不存在")
    try:
        got = await fetch_image(image_id)
    except ImageStoreError as error:
        logger.warning("取生成图失败: image_id=%s error=%s", image_id, error)
        raise ApiException(ErrorCode.INTERNAL_ERROR, "图片暂时取不到", 502) from None
    if got is None:
        raise ApiException.not_found("图片不存在")
    data, mime_type = got
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
