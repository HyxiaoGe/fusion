"""站内通知：业务终态同事务入队，分页与已读共用用户修订水位。"""

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.db.models import AgentSession, Conversation, Notification
from app.db.notification_repository import NotificationRepository
from app.schemas.notification import (
    MAX_NOTIFICATION_REVISION,
    NotificationFilter,
    NotificationItem,
    NotificationPage,
    NotificationReadResult,
    NotificationTarget,
)
from app.schemas.response import ApiException
from app.utils.time import as_utc

_TERMINAL_COPY = {
    "completed": ("run_completed", "已完成", "回答已保存，点击查看结果。"),
    "error": ("run_failed", "生成失败", "本次生成未能完成，点击查看详情或重试。"),
    "limit_reached": ("run_limit_reached", "需要继续", "本次运行已达到上限，可打开对话继续。"),
    "incomplete": ("run_incomplete", "尚未完成", "本次运行未能得到完整结果，可打开对话重试。"),
    "interrupted": ("run_interrupted", "生成中断", "运行已中断，可打开对话查看已保存的内容并重试。"),
}


def enqueue_run_notification(
    db: Session, run: AgentSession, *, notify_interrupted: bool = False
) -> Notification | None:
    """在调用方的业务终态事务中登记通知；不提交，不发外部信号。"""
    if run.status not in _TERMINAL_COPY or not run.message_id:
        return None
    if run.status == "interrupted" and not notify_interrupted:
        return None
    source = db.execute(
        select(Conversation.user_id, Conversation.title).where(Conversation.id == run.conversation_id)
    ).one_or_none()
    if source is None or source.user_id != run.user_id:
        raise ValueError("通知来源对话与运行的用户归属不符")
    kind, suffix, body = _TERMINAL_COPY[run.status]
    # 保存来源标题快照，截断时保留终态后缀，多个并发对话仍能直接区分。
    label = source.title.strip() or "对话"
    title = label[: 120 - len(suffix)] + suffix
    return NotificationRepository(db).enqueue(
        user_id=run.user_id,
        run_id=run.id,
        kind=kind,
        conversation_id=run.conversation_id,
        message_id=run.message_id,
        title=title,
        body=body,
    )


def get_unread_conversation_ids(db: Session, user_id: str) -> list[str]:
    """读取通知表的未读对话集合；不提交调用方事务。"""
    return NotificationRepository(db).unread_conversation_ids(user_id)


def _item(row: Notification) -> NotificationItem:
    return NotificationItem(
        id=row.id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        created_at=as_utc(row.created_at),
        read_at=as_utc(row.read_at) if row.read_at is not None else None,
        created_revision=row.created_revision,
        target=NotificationTarget(conversation_id=row.conversation_id, message_id=row.message_id, run_id=row.run_id),
    )


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = NotificationRepository(db)

    def list_notifications(
        self,
        user_id: str,
        *,
        filter: NotificationFilter = "all",
        cursor: str | None = None,
        limit: int = 20,
    ) -> NotificationPage:
        if not 1 <= limit <= 100 or filter not in ("all", "unread"):
            raise ApiException.bad_request("通知分页参数无效")
        before = None
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdigit() or not 0 < int(cursor) <= MAX_NOTIFICATION_REVISION:
                raise ApiException.bad_request("通知分页游标无效")
            before = int(cursor)
        state = self.repository.lock_state(user_id, shared=True)
        if state is None:
            return NotificationPage(items=[], unread_count=0, unread_conversation_ids=[], revision=0, next_cursor=None)
        query = select(Notification).where(Notification.user_id == user_id)
        if filter == "unread":
            query = query.where(Notification.read_at.is_(None))
        if before is not None:
            query = query.where(Notification.created_revision < before)
        rows = list(self.db.scalars(query.order_by(Notification.created_revision.desc()).limit(limit + 1)))
        page = NotificationPage(
            items=[_item(row) for row in rows[:limit]],
            unread_count=self.repository.unread_count(user_id),
            unread_conversation_ids=self.repository.unread_conversation_ids(user_id),
            revision=state.revision,
            next_cursor=str(rows[limit - 1].created_revision) if len(rows) > limit else None,
        )
        # 快照已完全序列化，立即释放共享锁，不能在发送响应期间阻塞业务终态。
        self.db.commit()
        return page

    def mark_read(self, user_id: str, ids: list[str]) -> NotificationReadResult:
        return self._mark_read(user_id, Notification.id.in_(set(ids)))

    def mark_all_read(self, user_id: str, through_revision: int) -> NotificationReadResult:
        if not 0 <= through_revision <= MAX_NOTIFICATION_REVISION:
            raise ApiException.bad_request("通知修订水位无效")
        return self._mark_read(
            user_id,
            Notification.created_revision <= through_revision,
            through_revision=through_revision,
        )

    def mark_result_read(
        self, user_id: str, *, conversation_id: str, results: list[dict[str, str]]
    ) -> NotificationReadResult:
        if not 1 <= len(results) <= 100:
            raise ApiException.bad_request("结果数量必须介于 1 到 100 项")
        references = {(result["run_id"], result["message_id"]) for result in results}
        runs = list(
            self.db.scalars(
                select(AgentSession)
                .join(Conversation, Conversation.id == AgentSession.conversation_id)
                .where(
                    tuple_(AgentSession.id, AgentSession.message_id).in_(references),
                    AgentSession.user_id == user_id,
                    AgentSession.conversation_id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
        )
        if len(runs) != len(references):
            raise ApiException.not_found("结果不存在或无权访问")
        if any(run.status not in _TERMINAL_COPY or run.terminal_at is None for run in runs):
            raise ApiException.conflict("结果尚未保存，请稍后重试")
        return self._mark_read(
            user_id,
            Notification.conversation_id == conversation_id,
            tuple_(Notification.run_id, Notification.message_id).in_(references),
        )

    def _mark_read(self, user_id: str, *criteria, through_revision: int | None = None) -> NotificationReadResult:
        state = self.repository.lock_state(user_id)
        if through_revision is not None and through_revision > (state.revision if state is not None else 0):
            raise ApiException.bad_request("通知修订水位超过当前版本")
        updated_count = self.repository.read(user_id, state, *criteria) if state is not None else 0
        result = NotificationReadResult(
            updated_count=updated_count,
            unread_count=self.repository.unread_count(user_id) if state is not None else 0,
            unread_conversation_ids=self.repository.unread_conversation_ids(user_id) if state is not None else [],
            revision=state.revision if state is not None else 0,
        )
        self.db.commit()
        return result
