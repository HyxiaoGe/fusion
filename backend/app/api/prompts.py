# app/api/prompts.py
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_admin_audit_service, get_current_admin_user
from app.db.database import get_db
from app.db.models import User as UserModel
from app.schemas.response import success
from app.services import generated_images, home_showcase_service
from app.services.admin_audit_service import AdminAuditService
from app.services.external.agent_tools_images import ImageStoreError, fetch_image
from app.services.prompt_examples_service import get_prompt_examples

router = APIRouter()
admin_router = APIRouter()


class ShowcaseFromGeneratedRequest(BaseModel):
    image_id: str = Field(min_length=1, max_length=64)
    label: str
    prompt: str
    object_position: str | None = None


@router.get("/examples")
async def get_examples(
    request: Request,
    limit: int = Query(default=8, ge=1, le=50, description="返回数量，传大值可获取全量池"),
):
    """获取动态示例问题（无需鉴权，供首页展示）"""
    data = await get_prompt_examples(limit)
    return success(data=data, request_id=request.state.request_id)


@router.get("/showcase")
def get_showcase(request: Request, db: Session = Depends(get_db)):
    """首页生图示例卡片（无需鉴权，前端按用户是否开通生图决定展示）"""
    rows = home_showcase_service.list_showcase_images(db)
    return success(
        data={"images": [home_showcase_service.serialize_showcase_image(row) for row in rows]},
        request_id=request.state.request_id,
    )


@router.get("/showcase/{image_id}/image")
async def get_showcase_image(image_id: str, db: Session = Depends(get_db)):
    return await _showcase_image_response(db, image_id, full=False)


@router.get("/showcase/{image_id}/image/full")
async def get_showcase_full_image(image_id: str, db: Session = Depends(get_db)):
    """点开卡片查看细节用的大图"""
    return await _showcase_image_response(db, image_id, full=True)


async def _showcase_image_response(db: Session, image_id: str, *, full: bool) -> Response:
    content = await home_showcase_service.read_showcase_image(db, image_id, full=full)
    if content is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    # 每张图的 id 唯一且内容不再变化，可以长期缓存
    return Response(
        content=content,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@admin_router.get("")
def list_showcase_admin(
    request: Request,
    db: Session = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin_user),
):
    rows = home_showcase_service.list_showcase_images(db)
    return success(
        data={"images": [home_showcase_service.serialize_showcase_image(row) for row in rows]},
        request_id=request.state.request_id,
    )


@admin_router.get("/generated")
async def list_generated_for_showcase(
    request: Request,
    offset: int = Query(default=0, ge=0),
    hide_in_pool: bool = Query(default=False),
    db: Session = Depends(get_db),
    admin: UserModel = Depends(get_current_admin_user),
):
    """当前管理员自己生成过的图片，按编辑链分组，供挑进图片池；不列其他用户的图。"""
    in_pool = frozenset(
        row.source_image_id for row in home_showcase_service.list_showcase_images(db) if row.source_image_id
    )
    page, next_offset = home_showcase_service.list_generated_groups(
        db, str(admin.id), exclude_image_ids=in_pool if hide_in_pool else frozenset(), offset=offset
    )
    versions = [image["image_id"] for group in page for image in group["versions"]]
    # 只列还记在 generated_images 里、归当前管理员的图（会话删了的取不到）
    available = generated_images.user_image_ids(db, image_ids=versions, user_id=str(admin.id))
    groups = []
    for group in page:
        items = [
            {
                **image,
                "thumbnail_url": generated_images.signed_image_url(image["image_id"]),
                "in_pool": image["image_id"] in in_pool,
            }
            for image in group["versions"]
            if image["image_id"] in available
        ]
        if items:
            groups.append({"key": group["key"], "versions": items})
    return success(data={"groups": groups, "next_offset": next_offset}, request_id=request.state.request_id)


@admin_router.post("/from-generated")
async def add_showcase_from_generated(
    payload: ShowcaseFromGeneratedRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: UserModel = Depends(get_current_admin_user),
    audit_service: AdminAuditService = Depends(get_admin_audit_service),
):
    # 只认当前管理员自己画的图，别人的图取不到
    if generated_images.user_image(db, image_id=payload.image_id, user_id=str(admin.id)) is None:
        raise HTTPException(status_code=404, detail="图片不存在或无权访问")
    try:
        got = await fetch_image(payload.image_id)
    except ImageStoreError:
        raise HTTPException(status_code=502, detail="图片暂时取不到，请稍后再试") from None
    if got is None:
        raise HTTPException(status_code=404, detail="图片不存在或无权访问")
    row = await home_showcase_service.add_showcase_image(
        db,
        content=got[0],
        label=payload.label,
        prompt=payload.prompt,
        object_position=payload.object_position,
        created_by=str(admin.id),
        source_image_id=payload.image_id,
    )
    audit_service._record(
        admin=admin,
        action="admin.home_showcase.create",
        resource_type="home_showcase_image",
        resource_id=row.id,
        request_id=request.state.request_id,
        metadata={"label": row.label, "source_image_id": payload.image_id},
    )
    return success(data=home_showcase_service.serialize_showcase_image(row), request_id=request.state.request_id)


@admin_router.delete("/{image_id}")
async def delete_showcase_admin(
    image_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: UserModel = Depends(get_current_admin_user),
    audit_service: AdminAuditService = Depends(get_admin_audit_service),
):
    if not await home_showcase_service.delete_showcase_image(db, image_id):
        raise HTTPException(status_code=404, detail="图片不存在")
    audit_service._record(
        admin=admin,
        action="admin.home_showcase.delete",
        resource_type="home_showcase_image",
        resource_id=image_id,
        request_id=request.state.request_id,
    )
    return success(data={"id": image_id}, request_id=request.state.request_id)
