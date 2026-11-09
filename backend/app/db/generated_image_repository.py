"""generate_image 画的图属于哪段会话（generated_images 表；图本身存在 image-service 里）。

删会话、重新回答（旧回答被替换）、编辑问题（后面的回答被删）时，调用方在自己的事务里调 remove：
表里的行同事务删除，删掉的编号记在 session.info 里。服务层在事务提交后据此从 image-service 删图，
回滚则丢弃（见 app.services.generated_images）。只记表里确有的图：Fusion 只删自己登记过的图。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GeneratedImage

GENERATED_IMAGE_BLOCK = "generated_image"
# session.info 里本事务删掉的图片编号
REMOVED_IMAGE_IDS_KEY = "generated_images_removed"


def image_ids_in_blocks(blocks: Iterable[Any] | None) -> set[str]:
    """一条消息的内容块里指着的生成图编号（块是存库的 dict）。"""

    ids: set[str] = set()
    for block in blocks or []:
        if isinstance(block, dict) and block.get("type") == GENERATED_IMAGE_BLOCK:
            image_id = block.get("image_id")
            if isinstance(image_id, str) and image_id:
                ids.add(image_id)
    return ids


class GeneratedImageRepository:
    def __init__(self, db: Session):
        self.db = db

    def ids_for_conversation(self, conversation_id: str) -> set[str]:
        return set(self.db.scalars(select(GeneratedImage.id).where(GeneratedImage.conversation_id == conversation_id)))

    def remove(self, image_ids: Iterable[str]) -> None:
        """在调用方事务里删掉这些图的记录，并记下删了哪些，供提交后从服务里删图。"""

        ids = {image_id for image_id in image_ids if isinstance(image_id, str) and image_id}
        if not ids:
            return
        existing = set(self.db.scalars(select(GeneratedImage.id).where(GeneratedImage.id.in_(ids))))
        if not existing:
            return
        self.db.query(GeneratedImage).filter(GeneratedImage.id.in_(existing)).delete(synchronize_session=False)
        self.db.info.setdefault(REMOVED_IMAGE_IDS_KEY, set()).update(existing)

    def remove_for_conversation(self, conversation_id: str) -> None:
        self.remove(self.ids_for_conversation(conversation_id))
