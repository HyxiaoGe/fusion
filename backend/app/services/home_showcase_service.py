"""首页生图示例卡片的图片池。

管理员上传图片并写好「做同款」提示词，首页按添加顺序轮换展示。图片统一转成短边 480px 的 WebP
存进文件存储，经公开接口读取：首页是匿名可见的，不能用用户私有文件的签名地址。
"""

from __future__ import annotations

import asyncio
import io
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.db.models import HomeShowcaseImage
from app.services.storage import get_storage, get_storage_for_backend

MAX_LABEL_CHARS = 12
MAX_PROMPT_CHARS = 500
OBJECT_POSITIONS = frozenset({"top", "bottom"})
# 卡片在桌面端约 184px 宽，480px 短边覆盖 2 倍屏
_SHORT_SIDE = 480
_WEBP_QUALITY = 82
_STORAGE_PREFIX = "home-showcase"


class ShowcaseImageError(ValueError):
    """上传的图片或文字不符合要求，信息可直接展示给管理员。"""


def list_showcase_images(db: Session) -> list[HomeShowcaseImage]:
    return db.query(HomeShowcaseImage).order_by(HomeShowcaseImage.created_at.asc(), HomeShowcaseImage.id.asc()).all()


def serialize_showcase_image(row: HomeShowcaseImage) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "prompt": row.prompt,
        "object_position": row.object_position,
        "image_url": f"/api/prompts/showcase/{row.id}/image",
        "width": row.width,
        "height": row.height,
    }


def _normalize_text(value: str, *, field: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        raise ShowcaseImageError(f"{field}不能为空")
    if len(text) > limit:
        raise ShowcaseImageError(f"{field}最多 {limit} 个字")
    return text


def _to_webp(content: bytes) -> tuple[bytes, int, int]:
    try:
        image = Image.open(io.BytesIO(content))
        image = ImageOps.exif_transpose(image) or image
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ShowcaseImageError("图片无法读取，请换一张") from exc
    scale = _SHORT_SIDE / min(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=_WEBP_QUALITY, method=6)
    return output.getvalue(), image.width, image.height


async def add_showcase_image(
    db: Session,
    *,
    content: bytes,
    label: str,
    prompt: str,
    object_position: str | None,
    created_by: str,
) -> HomeShowcaseImage:
    label = _normalize_text(label, field="标签", limit=MAX_LABEL_CHARS)
    prompt = _normalize_text(prompt, field="提示词", limit=MAX_PROMPT_CHARS)
    object_position = (object_position or "").strip() or None
    if object_position is not None and object_position not in OBJECT_POSITIONS:
        raise ShowcaseImageError("裁切位置只能是 top 或 bottom")
    if not content:
        raise ShowcaseImageError("请上传图片")
    if len(content) > settings.MAX_FILE_SIZE:
        raise ShowcaseImageError("图片超过大小上限")

    data, width, height = await asyncio.to_thread(_to_webp, content)
    image_id = str(uuid.uuid4())
    storage_key = f"{_STORAGE_PREFIX}/{image_id}.webp"
    storage = get_storage()
    await storage.upload(storage_key, data, "image/webp")
    row = HomeShowcaseImage(
        id=image_id,
        label=label,
        prompt=prompt,
        object_position=object_position,
        storage_key=storage_key,
        storage_backend=settings.STORAGE_BACKEND,
        width=width,
        height=height,
        created_by=created_by,
    )
    try:
        db.add(row)
        db.commit()
    except BaseException:
        db.rollback()
        await _delete_object(settings.STORAGE_BACKEND, storage_key)
        raise
    db.refresh(row)
    return row


async def delete_showcase_image(db: Session, image_id: str) -> bool:
    row = db.get(HomeShowcaseImage, image_id)
    if row is None:
        return False
    backend, storage_key = row.storage_backend, row.storage_key
    db.delete(row)
    db.commit()
    await _delete_object(backend, storage_key)
    return True


async def read_showcase_image(db: Session, image_id: str) -> bytes | None:
    row = db.get(HomeShowcaseImage, image_id)
    if row is None:
        return None
    return await get_storage_for_backend(row.storage_backend).download(row.storage_key)


async def _delete_object(backend: str, storage_key: str) -> None:
    try:
        await get_storage_for_backend(backend).delete(storage_key)
    except Exception:
        # 记录已删，首页不会再引用；对象残留只占空间，不影响展示
        logger.warning("首页示例图对象删除失败: %s", storage_key, exc_info=True)
