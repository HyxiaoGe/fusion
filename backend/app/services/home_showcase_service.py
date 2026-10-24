"""首页生图示例卡片的图片池。

管理员上传图片或从自己生成过的图里挑，写好「做同款」提示词，首页按添加顺序轮换展示。每张图存两份 WebP：
短边 480px 的卡片图，和长边 1600px 的大图（点开卡片查看细节用），经公开接口读取：首页是匿名可见的，
不能用用户私有文件的签名地址。
"""

from __future__ import annotations

import asyncio
import io
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import Text, and_, cast, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.db.models import Conversation, HomeShowcaseImage, Message
from app.services.storage import get_storage, get_storage_for_backend

MAX_LABEL_CHARS = 12
# 与生图提示词上限一致，挑生成图时可以直接沿用原提示词
MAX_PROMPT_CHARS = 1000
OBJECT_POSITIONS = frozenset({"top", "bottom"})
# 卡片在桌面端约 184px 宽，480px 短边覆盖 2 倍屏
_SHORT_SIDE = 480
_WEBP_QUALITY = 82
# 点开卡片看细节的大图
_FULL_LONG_SIDE = 1600
_FULL_WEBP_QUALITY = 85
_STORAGE_PREFIX = "home-showcase"
# 挑选列表每页看这么多条带生成图的回复（通常一条一张图），往后翻页继续
GENERATED_PAGE_MESSAGES = 20


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
        "source_file_id": row.source_file_id,
        "image_url": f"/api/prompts/showcase/{row.id}/image",
        "full_image_url": f"/api/prompts/showcase/{row.id}/image/full",
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


def full_storage_key(image_id: str) -> str:
    return f"{_STORAGE_PREFIX}/{image_id}-full.webp"


def _scaled_webp(image: Image.Image, scale: float, quality: int) -> tuple[bytes, int, int]:
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=quality, method=6)
    return output.getvalue(), image.width, image.height


def _to_webp(content: bytes) -> tuple[bytes, int, int, bytes]:
    """返回卡片图（及其宽高）和大图。"""
    try:
        image = Image.open(io.BytesIO(content))
        image = ImageOps.exif_transpose(image) or image
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ShowcaseImageError("图片无法读取，请换一张") from exc
    card, width, height = _scaled_webp(image, _SHORT_SIDE / min(image.size), _WEBP_QUALITY)
    full, _, _ = _scaled_webp(image, _FULL_LONG_SIDE / max(image.size), _FULL_WEBP_QUALITY)
    return card, width, height, full


async def add_showcase_image(
    db: Session,
    *,
    content: bytes,
    label: str,
    prompt: str,
    object_position: str | None,
    created_by: str,
    source_file_id: str | None = None,
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

    data, width, height, full_data = await asyncio.to_thread(_to_webp, content)
    image_id = str(uuid.uuid4())
    storage_key = f"{_STORAGE_PREFIX}/{image_id}.webp"
    full_key = full_storage_key(image_id)
    storage = get_storage()
    await storage.upload(storage_key, data, "image/webp")
    try:
        await storage.upload(full_key, full_data, "image/webp")
    except BaseException:
        await _delete_object(settings.STORAGE_BACKEND, storage_key)
        raise
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
        source_file_id=source_file_id,
    )
    try:
        db.add(row)
        db.commit()
    except BaseException:
        db.rollback()
        await _delete_object(settings.STORAGE_BACKEND, storage_key)
        await _delete_object(settings.STORAGE_BACKEND, full_key)
        raise
    db.refresh(row)
    return row


def list_generated_images(
    db: Session,
    user_id: str,
    *,
    after_message_id: str | None = None,
    page_messages: int = GENERATED_PAGE_MESSAGES,
) -> tuple[list[dict], str | None]:
    """该用户生成过的图片（新的在前），带生成时的名称和提示词，供挑进图片池。

    按回复分页：``after_message_id`` 是上一页最后一条回复，返回本页图片和下一页的起点（没有更多时为 None）。
    """
    query = (
        db.query(Message.id, Message.content)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.user_id == user_id,
            Message.role == "assistant",
            cast(Message.content, Text).like('%"generated_image"%'),
        )
    )
    if after_message_id is not None:
        cursor_at = db.query(Message.created_at).filter(Message.id == after_message_id).scalar_subquery()
        query = query.filter(
            or_(
                Message.created_at < cursor_at,
                and_(Message.created_at == cursor_at, Message.id < after_message_id),
            )
        )
    rows = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(page_messages).all()
    images: list[dict] = []
    seen: set[str] = set()
    for _message_id, content in rows:
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "generated_image":
                continue
            file_id = block.get("file_id")
            if not isinstance(file_id, str) or not file_id or file_id in seen:
                continue
            seen.add(file_id)
            images.append(
                {
                    "file_id": file_id,
                    "prompt": str(block.get("prompt") or ""),
                    "title": block.get("title") if isinstance(block.get("title"), str) else None,
                    "width": block.get("width"),
                    "height": block.get("height"),
                }
            )
    next_cursor = rows[-1][0] if len(rows) == page_messages else None
    return images, next_cursor


async def delete_showcase_image(db: Session, image_id: str) -> bool:
    row = db.get(HomeShowcaseImage, image_id)
    if row is None:
        return False
    backend, storage_key = row.storage_backend, row.storage_key
    db.delete(row)
    db.commit()
    await _delete_object(backend, storage_key)
    await _delete_object(backend, full_storage_key(image_id))
    return True


async def read_showcase_image(db: Session, image_id: str, *, full: bool = False) -> bytes | None:
    row = db.get(HomeShowcaseImage, image_id)
    if row is None:
        return None
    key = full_storage_key(row.id) if full else row.storage_key
    try:
        return await get_storage_for_backend(row.storage_backend).download(key)
    except FileNotFoundError:
        return None


async def _delete_object(backend: str, storage_key: str) -> None:
    try:
        await get_storage_for_backend(backend).delete(storage_key)
    except Exception:
        # 记录已删，首页不会再引用；对象残留只占空间，不影响展示
        logger.warning("首页示例图对象删除失败: %s", storage_key, exc_info=True)
