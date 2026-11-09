"""生成图的归属与清理。

图存在 image-service 里（见 app.services.external.agent_tools_images），这里记它属于哪段会话（generated_images 表）：
- 页面取图、改图时传来的原图编号，都按这张表认会话；不在表里的编号一律当作没有这张图。
- 删会话、重新回答（旧回答被替换）、编辑问题（后面的回答被删）时，数据层在同一事务里删掉记录
  （GeneratedImageRepository.remove）；这里在事务提交后把删掉的图从服务里删掉，回滚就什么也不删。
  服务删不掉只记日志：留下的图没人指着，只占服务的磁盘。
  提交后的监听随本模块导入注册：API 进程经路由和生图工具都会导入。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.file_token import generate_file_token, verify_file_token
from app.core.logger import app_logger as logger
from app.db.generated_image_repository import REMOVED_IMAGE_IDS_KEY
from app.db.models import GeneratedImage
from app.services.external.agent_tools_images import delete_images, is_image_id

# 取图地址的签名范围：和文件的签名 token 共用密钥，加前缀免得拿去换文件。
_TOKEN_SCOPE = "generated_image:"


def register_generated_image(
    db: Session,
    *,
    image_id: str,
    conversation_id: str,
    user_id: str,
    mime_type: str,
) -> None:
    db.add(GeneratedImage(id=image_id, conversation_id=conversation_id, user_id=user_id, mime_type=mime_type))
    db.commit()


def conversation_has_image(db: Session, *, conversation_id: str, image_id: str) -> bool:
    if not is_image_id(image_id):
        return False
    return (
        db.scalar(
            select(GeneratedImage.id).where(
                GeneratedImage.id == image_id,
                GeneratedImage.conversation_id == conversation_id,
            )
        )
        is not None
    )


def user_image(db: Session, *, image_id: str, user_id: str) -> GeneratedImage | None:
    if not is_image_id(image_id):
        return None
    return db.scalar(select(GeneratedImage).where(GeneratedImage.id == image_id, GeneratedImage.user_id == user_id))


def user_image_ids(db: Session, *, image_ids: Iterable[str], user_id: str) -> set[str]:
    ids = [image_id for image_id in image_ids if is_image_id(image_id)]
    if not ids:
        return set()
    return set(
        db.scalars(select(GeneratedImage.id).where(GeneratedImage.id.in_(ids), GeneratedImage.user_id == user_id))
    )


def image_exists(db: Session, image_id: str) -> bool:
    return is_image_id(image_id) and db.get(GeneratedImage, image_id) is not None


def signed_image_url(image_id: str) -> str:
    """页面用 <img src> 取图，带不了登录信息：签一个短期 token，内容接口凭它取图。"""

    token = generate_file_token(f"{_TOKEN_SCOPE}{image_id}", settings.MINIO_PRESIGN_EXPIRES)
    return f"/api/generated-images/{image_id}/content?token={token}"


def verify_image_token(token: str, image_id: str) -> bool:
    return verify_file_token(token) == f"{_TOKEN_SCOPE}{image_id}"


def _delete_in_background(image_ids: set[str]) -> None:
    if not settings.AGENT_TOOLS_API_KEY.strip():
        logger.warning("未配置 AGENT_TOOLS_API_KEY，跳过删除生成图: count=%s", len(image_ids))
        return

    def run() -> None:
        try:
            delete_images(sorted(image_ids))
        except Exception as error:  # noqa: BLE001 — 后台清理失败只记日志
            logger.warning("删除生成图异常: error_type=%s", type(error).__name__)

    threading.Thread(target=run, name="generated-image-delete", daemon=True).start()


# 测试替换成记录调用的函数
schedule_image_deletion: Callable[[set[str]], None] = _delete_in_background


@event.listens_for(Session, "after_commit")
def _delete_after_commit(session: Session) -> None:
    ids = session.info.pop(REMOVED_IMAGE_IDS_KEY, None)
    if ids:
        schedule_image_deletion(set(ids))


@event.listens_for(Session, "after_soft_rollback")
def _discard_after_rollback(session: Session, previous_transaction: Any) -> None:
    if not getattr(previous_transaction, "nested", False):
        session.info.pop(REMOVED_IMAGE_IDS_KEY, None)
