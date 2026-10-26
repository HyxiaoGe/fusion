"""通知增加额度类：发放重置卡、管理员重置额度时提醒用户，无来源对象。"""

import sqlalchemy as sa

from alembic import op

revision = "d7a3c9e1f2b5"
down_revision = "c5f2a8d3e614"
branch_labels = None
depends_on = None

_AI_AND_CHANGELOG = (
    "(business_type = 'ai_conversation' AND kind IN "
    "('run_completed', 'run_failed', 'run_limit_reached', 'run_incomplete', 'run_interrupted') "
    "AND conversation_id IS NOT NULL AND message_id IS NOT NULL AND run_id IS NOT NULL AND changelog_id IS NULL) "
    "OR (business_type = 'changelog' AND kind = 'changelog_published' AND changelog_id IS NOT NULL "
    "AND conversation_id IS NULL AND message_id IS NULL AND run_id IS NULL)"
)
SOURCE_CHECK = (
    _AI_AND_CHANGELOG + " OR (business_type = 'credits' AND kind IN ('reset_cards_granted', 'credits_reset') "
    "AND conversation_id IS NULL AND message_id IS NULL AND run_id IS NULL AND changelog_id IS NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_source", type_="check")
        batch.create_check_constraint("ck_notifications_source", SOURCE_CHECK)


def downgrade() -> None:
    # 额度通知无法转换成旧来源；降级需先由发布负责人明确处理，不能静默丢数据。
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM notifications WHERE business_type = 'credits'")):
        raise RuntimeError("仍有额度通知，不能无损降级")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_source", type_="check")
        batch.create_check_constraint("ck_notifications_source", _AI_AND_CHANGELOG)
