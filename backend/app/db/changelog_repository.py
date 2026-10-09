"""更新日志不可变发布记录与稳定分页。"""

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import Changelog, Notification, User
from app.utils.time import utc_now


class ChangelogRepository:
    def __init__(self, db: Session):
        self.db = db

    def insert_publication(self, payload: dict[str, str]) -> tuple[Changelog, bool]:
        """唯一版本竞争者等首次发布提交后读取；仅首次插入者负责原子广播。"""
        dialect = self.db.get_bind().dialect.name
        insert = postgresql_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
        if insert is None:
            raise RuntimeError("更新日志不支持当前数据库方言")
        created_id = self.db.scalar(
            insert(Changelog)
            .values(id=str(uuid.uuid4()), published_at=utc_now(), **payload)
            .on_conflict_do_nothing(index_elements=["version"])
            .returning(Changelog.id)
        )
        row = self.db.scalar(select(Changelog).where(Changelog.version == payload["version"]))
        return row, created_id is not None

    def recipient_ids(self) -> list[str]:
        # 本地 User 没有停用字段；固定升序避免不同版本广播交叉锁住用户水位。
        return list(self.db.scalars(select(User.id).order_by(User.id)))

    def get(self, changelog_id: str) -> Changelog | None:
        return self.db.get(Changelog, changelog_id)

    def list(self, before: Changelog | None, limit: int) -> list[Changelog]:
        query = select(Changelog)
        if before is not None:
            query = query.where(
                or_(
                    Changelog.published_at < before.published_at,
                    and_(Changelog.published_at == before.published_at, Changelog.id < before.id),
                )
            )
        return list(self.db.scalars(query.order_by(Changelog.published_at.desc(), Changelog.id.desc()).limit(limit)))

    def notification_id(self, changelog_id: str, user_id: str) -> str | None:
        return self.db.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id,
                Notification.business_type == "changelog",
                Notification.changelog_id == changelog_id,
            )
        )

    def notification_ids(self, changelog_ids: Sequence[str], user_id: str) -> dict[str, str]:
        if not changelog_ids:
            return {}
        rows = self.db.execute(
            select(Notification.changelog_id, Notification.id).where(
                Notification.user_id == user_id,
                Notification.business_type == "changelog",
                Notification.changelog_id.in_(changelog_ids),
            )
        )
        return {changelog_id: notification_id for changelog_id, notification_id in rows}
