"""更新日志发布与当前已有用户通知在单一事务内提交。"""

from sqlalchemy.orm import Session

from app.db.changelog_repository import ChangelogRepository
from app.db.models import Changelog
from app.db.notification_repository import NotificationRepository
from app.schemas.changelog import ChangelogDetail, ChangelogPage, ChangelogPublishRequest, ChangelogSummary
from app.schemas.response import ApiException
from app.utils.time import as_utc


def _summary(row: Changelog) -> ChangelogSummary:
    return ChangelogSummary(
        id=row.id, version=row.version, title=row.title, summary=row.summary, published_at=as_utc(row.published_at)
    )


class ChangelogService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = ChangelogRepository(db)

    def publish(self, user_id: str, request: ChangelogPublishRequest) -> ChangelogDetail:
        payload = request.model_dump()
        try:
            row, created = self.repository.insert_publication(payload)
            if any(getattr(row, key) != value for key, value in payload.items()):
                raise ApiException.conflict("该版本已发布不同内容")
            if created:
                notifications = NotificationRepository(self.db)
                for recipient_id in self.repository.recipient_ids():
                    notifications.enqueue_changelog(
                        user_id=recipient_id, changelog_id=row.id, title=row.title, body=row.summary
                    )
            detail = self._detail(row, user_id)
            self.db.commit()
            return detail
        except BaseException:
            self.db.rollback()
            raise

    def list_changelogs(self, *, cursor: str | None = None, limit: int = 20) -> ChangelogPage:
        if not 1 <= limit <= 100:
            raise ApiException.bad_request("更新日志分页参数无效")
        before = self.repository.get(cursor) if cursor else None
        if cursor is not None and before is None:
            raise ApiException.bad_request("更新日志分页游标无效")
        rows = self.repository.list(before, limit + 1)
        return ChangelogPage(
            items=[_summary(row) for row in rows[:limit]], next_cursor=rows[limit - 1].id if len(rows) > limit else None
        )

    def get_changelog(self, user_id: str, changelog_id: str) -> ChangelogDetail:
        row = self.repository.get(changelog_id)
        if row is None:
            raise ApiException.not_found("更新日志不存在")
        return self._detail(row, user_id)

    def _detail(self, row: Changelog, user_id: str) -> ChangelogDetail:
        return ChangelogDetail(
            **_summary(row).model_dump(),
            content=row.content,
            notification_id=self.repository.notification_id(row.id, user_id),
        )
