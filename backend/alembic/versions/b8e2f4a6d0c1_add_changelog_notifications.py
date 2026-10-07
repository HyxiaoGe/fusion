"""增加更新日志与通知业务分类，旧通知保留为 AI 对话类型。"""

import sqlalchemy as sa

from alembic import op

revision = "b8e2f4a6d0c1"
down_revision = "6a1e9f3c8b20"
branch_labels = None
depends_on = None

SOURCE_CHECK = (
    "(business_type = 'ai_conversation' AND kind IN "
    "('run_completed', 'run_failed', 'run_limit_reached', 'run_incomplete', 'run_interrupted') "
    "AND conversation_id IS NOT NULL AND message_id IS NOT NULL AND run_id IS NOT NULL AND changelog_id IS NULL) "
    "OR (business_type = 'changelog' AND kind = 'changelog_published' AND changelog_id IS NOT NULL "
    "AND conversation_id IS NULL AND message_id IS NULL AND run_id IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "changelogs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_changelogs_version"),
    )
    op.create_index("ix_changelogs_published_id", "changelogs", ["published_at", "id"])
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("business_type", sa.String(32), server_default="ai_conversation", nullable=False))
        batch.add_column(sa.Column("changelog_id", sa.String(), nullable=True))
        batch.alter_column("run_id", existing_type=sa.String(), nullable=True)
        batch.alter_column("conversation_id", existing_type=sa.String(), nullable=True)
        batch.alter_column("message_id", existing_type=sa.String(), nullable=True)
        batch.create_foreign_key("fk_notifications_changelog", "changelogs", ["changelog_id"], ["id"])
        batch.create_unique_constraint("uq_notifications_user_changelog", ["user_id", "changelog_id"])
        batch.create_check_constraint("ck_notifications_source", SOURCE_CHECK)


def downgrade() -> None:
    # 新业务通知无法转换成旧 AI 来源；降级需先由发布负责人明确处理，不能静默丢数据。
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM changelogs")):
        raise RuntimeError("仍有已发布的更新日志，不能无损降级")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_source", type_="check")
        batch.drop_constraint("uq_notifications_user_changelog", type_="unique")
        batch.drop_constraint("fk_notifications_changelog", type_="foreignkey")
        batch.alter_column("run_id", existing_type=sa.String(), nullable=False)
        batch.alter_column("conversation_id", existing_type=sa.String(), nullable=False)
        batch.alter_column("message_id", existing_type=sa.String(), nullable=False)
        batch.drop_column("changelog_id")
        batch.drop_column("business_type")
    op.drop_table("changelogs")
