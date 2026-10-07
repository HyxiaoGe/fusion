"""通知存储与用户修订锁；所有方法由调用方提交事务。"""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import Notification, NotificationUserState
from app.utils.time import utc_now


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def lock_state(self, user_id: str, *, create: bool = False, shared: bool = False) -> NotificationUserState | None:
        if create:
            dialect = self.db.get_bind().dialect.name
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
            if insert is None:
                raise RuntimeError("通知存储不支持当前数据库方言")
            self.db.execute(
                insert(NotificationUserState)
                .values(user_id=user_id, revision=0)
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
        # 全部写入都持有此锁；共享锁使列表、未读数和水位来自同一稳定状态。
        return self.db.execute(
            select(NotificationUserState)
            .where(NotificationUserState.user_id == user_id)
            .with_for_update(read=shared)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def enqueue(self, *, user_id: str, run_id: str, kind: str, **values) -> Notification:
        return self._enqueue(
            user_id=user_id,
            kind=kind,
            source_clause=Notification.run_id == run_id,
            business_type="ai_conversation",
            run_id=run_id,
            **values,
        )

    def enqueue_changelog(self, *, user_id: str, changelog_id: str, title: str, body: str) -> Notification:
        return self._enqueue(
            user_id=user_id,
            kind="changelog_published",
            source_clause=Notification.changelog_id == changelog_id,
            business_type="changelog",
            changelog_id=changelog_id,
            title=title,
            body=body,
        )

    def _enqueue(self, *, user_id: str, kind: str, source_clause, **values) -> Notification:
        state = self.lock_state(user_id, create=True)
        existing = self.db.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                source_clause,
                Notification.kind == kind,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        state.revision += 1
        notification = Notification(
            user_id=user_id,
            kind=kind,
            created_revision=state.revision,
            **values,
        )
        self.db.add(notification)
        self.db.flush()
        return notification

    def unread_count(self, user_id: str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                )
            )
            or 0
        )

    def unread_conversation_ids(self, user_id: str) -> list[str]:
        return list(
            self.db.scalars(
                select(Notification.conversation_id)
                .where(
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                    Notification.business_type == "ai_conversation",
                    Notification.conversation_id.is_not(None),
                )
                .distinct()
                .order_by(Notification.conversation_id)
            )
        )

    def read(self, user_id: str, state: NotificationUserState, *criteria) -> int:
        result = self.db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None), *criteria)
            .values(read_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        updated_count = int(result.rowcount or 0)
        if updated_count:
            state.revision += 1
            self.db.flush()
        return updated_count
