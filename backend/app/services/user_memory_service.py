"""用户记忆：按条存储的用户画像，全量注入主对话，用户可查看、删除、关闭。

条目由后台整理任务写入（不在对话中途写），这里只负责读取、渲染与用户侧管理。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import User, UserMemory

# 推荐分类及注入时的展示顺序；整理任务也可以新增分类，未知分类排在最后。
MEMORY_CATEGORIES: tuple[str, ...] = (
    "name",
    "residence",
    "workplace",
    "occupation",
    "health",
    "diet",
    "family",
    "interests",
    "response_style",
    "other",
)

MAX_MEMORY_CONTENT_CHARS = 200
# 注入上限：画像应当很短，超出时优先保留最近更新的条目。
MAX_MEMORY_PROMPT_CHARS = 2000


@dataclass(frozen=True)
class MemoryItem:
    id: str
    category: str
    content: str
    source_conversation_id: str | None
    updated_at: str


def _category_rank(category: str) -> int:
    try:
        return MEMORY_CATEGORIES.index(category)
    except ValueError:
        return len(MEMORY_CATEGORIES)


class UserMemoryService:
    def __init__(self, db: Session):
        self.db = db

    def list_items(self, user_id: str) -> list[MemoryItem]:
        rows = (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == user_id)
            .order_by(UserMemory.created_at.asc(), UserMemory.id.asc())
            .all()
        )
        rows.sort(key=lambda row: _category_rank(row.category))
        return [
            MemoryItem(
                id=row.id,
                category=row.category,
                content=row.content,
                source_conversation_id=row.source_conversation_id,
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
            )
            for row in rows
        ]

    def delete_item(self, user_id: str, memory_id: str) -> bool:
        deleted = (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == user_id, UserMemory.id == memory_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return bool(deleted)

    def clear(self, user_id: str) -> int:
        deleted = self.db.query(UserMemory).filter(UserMemory.user_id == user_id).delete(synchronize_session=False)
        self.db.commit()
        return int(deleted)

    def set_enabled(self, user_id: str, enabled: bool) -> bool:
        self.db.query(User).filter(User.id == user_id).update({User.memory_enabled: enabled}, synchronize_session=False)
        self.db.commit()
        return enabled

    def render_for_prompt(self, user: User | None) -> str | None:
        """渲染注入主对话的记忆正文；游客、关闭记忆或没有条目时返回 None。"""
        if user is None or user.is_guest or not user.memory_enabled:
            return None
        rows = (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == user.id)
            .order_by(UserMemory.updated_at.desc(), UserMemory.id.asc())
            .all()
        )
        kept: list[UserMemory] = []
        used = 0
        for row in rows:
            line_length = len(row.category) + len(row.content) + 4
            if used + line_length > MAX_MEMORY_PROMPT_CHARS:
                continue
            kept.append(row)
            used += line_length
        if not kept:
            return None
        kept.sort(key=lambda row: (_category_rank(row.category), row.created_at))
        return "\n".join(f"- {row.category}: {row.content}" for row in kept)
