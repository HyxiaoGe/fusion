"""产品回答正则改写层已移除，删除观测表中的改写字段。

Revision ID: c4e8a2f1d935
Revises: b2c7d9e4f610
Create Date: 2026-09-27 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e8a2f1d935"
down_revision = "b2c7d9e4f610"
branch_labels = None
depends_on = None

_REPAIR_COLUMNS = ("repair_enabled", "repair_available", "repair_applied", "repair_reason_code")


def upgrade() -> None:
    for column in _REPAIR_COLUMNS:
        op.drop_column("product_answer_observations", column)


def downgrade() -> None:
    op.add_column(
        "product_answer_observations",
        sa.Column("repair_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "product_answer_observations",
        sa.Column("repair_available", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "product_answer_observations",
        sa.Column("repair_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "product_answer_observations",
        sa.Column("repair_reason_code", sa.String(64), nullable=False, server_default=""),
    )
