"""通知分类整理为对话、更新、系统三类：额度通知归入系统类。"""

import sqlalchemy as sa

from alembic import op

revision = "e4b8d2a6c1f3"
down_revision = "d7a3c9e1f2b5"
branch_labels = None
depends_on = None

_AI_AND_CHANGELOG = (
    "(business_type = 'ai_conversation' AND kind IN "
    "('run_completed', 'run_failed', 'run_limit_reached', 'run_incomplete', 'run_interrupted') "
    "AND conversation_id IS NOT NULL AND message_id IS NOT NULL AND run_id IS NOT NULL AND changelog_id IS NULL) "
    "OR (business_type = 'changelog' AND kind = 'changelog_published' AND changelog_id IS NOT NULL "
    "AND conversation_id IS NULL AND message_id IS NULL AND run_id IS NULL)"
)
_SYSTEM_SOURCE = (
    " AND kind IN ('reset_cards_granted', 'credits_reset') "
    "AND conversation_id IS NULL AND message_id IS NULL AND run_id IS NULL AND changelog_id IS NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_source", type_="check")
    op.execute(sa.text("UPDATE notifications SET business_type = 'system' WHERE business_type = 'credits'"))
    with op.batch_alter_table("notifications") as batch:
        batch.create_check_constraint(
            "ck_notifications_source", f"{_AI_AND_CHANGELOG} OR (business_type = 'system'{_SYSTEM_SOURCE}"
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_source", type_="check")
    op.execute(sa.text("UPDATE notifications SET business_type = 'credits' WHERE business_type = 'system'"))
    with op.batch_alter_table("notifications") as batch:
        batch.create_check_constraint(
            "ck_notifications_source", f"{_AI_AND_CHANGELOG} OR (business_type = 'credits'{_SYSTEM_SOURCE}"
        )
