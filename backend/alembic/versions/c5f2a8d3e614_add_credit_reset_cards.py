"""新增重置卡表：管理员发放，用户用一张清零当前日、周窗口的已用量。

Revision ID: c5f2a8d3e614
Revises: b3e7d1f4a982
Create Date: 2026-10-26 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "c5f2a8d3e614"
down_revision = "b3e7d1f4a982"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_reset_cards",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by", sa.String(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_entry_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credit_reset_cards_user_expires", "credit_reset_cards", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_credit_reset_cards_user_expires", table_name="credit_reset_cards")
    op.drop_table("credit_reset_cards")
