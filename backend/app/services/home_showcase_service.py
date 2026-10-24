"""首页生图示例卡片的图片池。

管理员从自己生成过的图里挑，写好「做同款」提示词，首页每次进入时打乱顺序轮换展示。每张图存两份 WebP：
短边 480px 的卡片图，和长边 1600px 的大图（点开卡片查看细节用），经公开接口读取：首页是匿名可见的，
不能用用户私有文件的签名地址。
"""

from __future__ import annotations

import asyncio
import io
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import Text, cast
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.db.models import Conversation, HomeShowcaseImage, Message, ToolCallLog
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
# 挑选列表每页的组数（一组是一张图和它的各个编辑版本）
GENERATED_PAGE_GROUPS = 20


class ShowcaseImageError(ValueError):
    """图片或文字不符合要求，信息可直接展示给管理员。"""


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
        raise ShowcaseImageError("图片内容为空")
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


def list_generated_groups(
    db: Session,
    user_id: str,
    *,
    exclude_file_ids: frozenset[str] = frozenset(),
    offset: int = 0,
    page_size: int = GENERATED_PAGE_GROUPS,
) -> tuple[list[dict], int | None]:
    """该用户生成过的图片，按编辑链分组，供挑进图片池。

    同一组是一张图和由它编辑、重画出来的各个版本，依据生图时的工具调用记录（编辑带 source_image_id，
    重画带 regenerate，归到同会话里的上一张图）；不按画面相似度猜。每组版本新的在前，组按最新版本时间新的在前。
    ``exclude_file_ids`` 里的版本不列出（如已在图片池），整组都被排除时这组不出现。按组分页，返回本页和下一页的偏移；
    每组是 ``{"key": 链上最早那张图的 file_id, "versions": [...]}``，key 不随排除和新版本变化。
    """
    rows = (
        db.query(Message.conversation_id, Message.content)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.user_id == user_id,
            Message.role == "assistant",
            cast(Message.content, Text).like('%"generated_image"%'),
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    images: list[dict] = []
    log_ids: dict[str, str] = {}
    conversation_of: dict[str, str] = {}
    for conversation_id, content in rows:
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "generated_image":
                continue
            file_id = block.get("file_id")
            if not isinstance(file_id, str) or not file_id or file_id in conversation_of:
                continue
            conversation_of[file_id] = conversation_id
            if isinstance(block.get("tool_call_log_id"), str) and block["tool_call_log_id"]:
                log_ids[file_id] = block["tool_call_log_id"]
            images.append(
                {
                    "file_id": file_id,
                    "prompt": str(block.get("prompt") or ""),
                    "title": block.get("title") if isinstance(block.get("title"), str) else None,
                    "width": block.get("width"),
                    "height": block.get("height"),
                }
            )

    params_by_log = (
        dict(
            db.query(ToolCallLog.id, ToolCallLog.input_params)
            .filter(ToolCallLog.user_id == user_id, ToolCallLog.id.in_(list(log_ids.values())))
            .all()
        )
        if log_ids
        else {}
    )
    parent: dict[str, str] = {image["file_id"]: image["file_id"] for image in images}

    def root(file_id: str) -> str:
        while parent[file_id] != file_id:
            parent[file_id] = parent[parent[file_id]]
            file_id = parent[file_id]
        return file_id

    last_in_conversation: dict[str, str] = {}
    for image in images:
        file_id = image["file_id"]
        params = params_by_log.get(log_ids.get(file_id, ""))
        params = params if isinstance(params, dict) else {}
        source = params.get("source_image_id")
        if isinstance(source, str) and source in parent:
            parent[root(file_id)] = root(source)
        elif params.get("regenerate") is True and conversation_of[file_id] in last_in_conversation:
            parent[root(file_id)] = root(last_in_conversation[conversation_of[file_id]])
        last_in_conversation[conversation_of[file_id]] = file_id

    groups: dict[str, list[dict]] = {}
    for image in images:
        groups.setdefault(root(image["file_id"]), []).append(image)
    # images 按时间正序：组内倒过来新的在前，组按最新版本在时间线上的位置倒序
    timeline = {image["file_id"]: index for index, image in enumerate(images)}
    ordered = sorted(
        ({"key": key, "versions": list(reversed(versions))} for key, versions in groups.items()),
        key=lambda group: timeline[group["versions"][0]["file_id"]],
        reverse=True,
    )
    visible = []
    for group in ordered:
        kept = [image for image in group["versions"] if image["file_id"] not in exclude_file_ids]
        if kept:
            visible.append({"key": group["key"], "versions": kept})
    page = visible[offset : offset + page_size]
    next_offset = offset + page_size if offset + page_size < len(visible) else None
    return page, next_offset


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
