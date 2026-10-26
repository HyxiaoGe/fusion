"""Credits 流水允许 user_id 为空：表示全员重置。

Revision ID: a9c4e7b2d150
Revises: d7a3f1c9e520
Create Date: 2026-10-26 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "a9c4e7b2d150"
down_revision = "d7a3f1c9e520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("credit_ledger", "user_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # 回退前去掉全员重置记录，否则无法恢复非空约束
    op.execute("DELETE FROM credit_ledger WHERE user_id IS NULL")
    op.alter_column("credit_ledger", "user_id", existing_type=sa.String(), nullable=False)
