# app/api/prompts.py
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_admin_audit_service, get_current_admin_user, get_file_service
from app.db.database import get_db
from app.db.models import User as UserModel
from app.schemas.response import success
from app.services import home_showcase_service
from app.services.admin_audit_service import AdminAuditService
from app.services.file_service import FileService
from app.services.prompt_examples_service import get_prompt_examples

router = APIRouter()
admin_router = APIRouter()


class ShowcaseFromGeneratedRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
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
    file_service: FileService = Depends(get_file_service),
):
    """当前管理员自己生成过的图片，按编辑链分组，供挑进图片池；不列其他用户的图。"""
    in_pool = frozenset(
        row.source_file_id for row in home_showcase_service.list_showcase_images(db) if row.source_file_id
    )
    page, next_offset = home_showcase_service.list_generated_groups(
        db, str(admin.id), exclude_file_ids=in_pool if hide_in_pool else frozenset(), offset=offset
    )
    versions = [image for group in page for image in group["versions"]]
    # 签发缩略图地址要逐张确认存储对象存在，并发进行
    thumbnail_urls = await asyncio.gather(
        *(file_service.get_file_url(image["file_id"], str(admin.id), "thumbnail") for image in versions)
    )
    url_of = {image["file_id"]: url for image, url in zip(versions, thumbnail_urls, strict=True)}
    groups = []
    for group in page:
        items = [
            {**image, "thumbnail_url": url_of[image["file_id"]], "in_pool": image["file_id"] in in_pool}
            for image in group["versions"]
            if url_of[image["file_id"]] is not None
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
    file_service: FileService = Depends(get_file_service),
    audit_service: AdminAuditService = Depends(get_admin_audit_service),
):
    # 按当前管理员取文件，别人的图取不到
    file_content = await file_service.get_file_content(payload.file_id, str(admin.id), "processed")
    if file_content is None:
        raise HTTPException(status_code=404, detail="图片不存在或无权访问")
    row = await home_showcase_service.add_showcase_image(
        db,
        content=file_content[0],
        label=payload.label,
        prompt=payload.prompt,
        object_position=payload.object_position,
        created_by=str(admin.id),
        source_file_id=payload.file_id,
    )
    audit_service._record(
        admin=admin,
        action="admin.home_showcase.create",
        resource_type="home_showcase_image",
        resource_id=row.id,
        request_id=request.state.request_id,
        metadata={"label": row.label, "source_file_id": payload.file_id},
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
