"""增加站内通知与用户修订水位。

Revision ID: 6a1e9f3c8b20
Revises: d1f3b5a7c9e2
"""

import sqlalchemy as sa

from alembic import op

revision = "6a1e9f3c8b20"
down_revision = "d1f3b5a7c9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_user_states",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint("revision >= 0", name="ck_notification_user_states_revision"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("created_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "run_id", "kind", name="uq_notifications_user_run_kind"),
        sa.UniqueConstraint("user_id", "created_revision", name="uq_notifications_user_created_revision"),
        sa.CheckConstraint("created_revision > 0", name="ck_notifications_created_revision"),
    )
    op.create_index("ix_notifications_conversation_id", "notifications", ["conversation_id"])
    op.create_index(
        "ix_notifications_user_unread_revision",
        "notifications",
        ["user_id", "created_revision"],
        postgresql_where=sa.text("read_at IS NULL"),
        sqlite_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("notification_user_states")
