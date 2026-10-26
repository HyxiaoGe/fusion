"""Credits 流水：每个 run 结算一条，管理员重置额度记一条。

Revision ID: d7a3f1c9e520
Revises: c4e8a2d6f913
Create Date: 2026-10-26 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d7a3f1c9e520"
down_revision = "c4e8a2d6f913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True, unique=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("run_status", sa.String(length=30), nullable=True),
        sa.Column("charged", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("credits", sa.Float(), nullable=False, server_default="0"),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_credit_ledger_user_occurred", "credit_ledger", ["user_id", "occurred_at"])
    op.create_index("ix_credit_ledger_message_id", "credit_ledger", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_message_id", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_user_occurred", table_name="credit_ledger")
    op.drop_table("credit_ledger")
