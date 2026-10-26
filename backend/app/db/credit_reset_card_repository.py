"""重置卡写入：发卡同时通知收卡人；由调用方提交事务。"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import CreditResetCard
from app.db.notification_repository import NotificationRepository
from app.utils.time import as_china_time

RESET_CARD_VALID_DAYS = 30


def add_reset_cards(
    db: Session, user_ids: list[str], *, count: int, granted_by: str | None, granted_at: datetime
) -> list[CreditResetCard]:
    """给每个账号发 count 张卡，自发放起 RESET_CARD_VALID_DAYS 天内有效；granted_by 为空表示系统发放。

    同时通知收卡人，否则只有点开额度面板才会发现，卡可能放到过期。
    """
    expires_at = granted_at + timedelta(days=RESET_CARD_VALID_DAYS)
    cards = [
        CreditResetCard(user_id=user_id, granted_by=granted_by, granted_at=granted_at, expires_at=expires_at)
        for user_id in user_ids
        for _ in range(count)
    ]
    db.add_all(cards)
    expires = as_china_time(expires_at)
    body = f"可在额度面板中使用，清零今日和本周已用额度，{expires.month} 月 {expires.day} 日 {expires:%H:%M} 前有效。"
    notifications = NotificationRepository(db)
    # 固定升序，和更新日志广播一致，避免交叉锁住用户通知水位。
    for user_id in sorted(user_ids):
        notifications.enqueue_system(
            user_id=user_id, kind="reset_cards_granted", title=f"你收到了 {count} 张额度重置卡", body=body
        )
    return cards
