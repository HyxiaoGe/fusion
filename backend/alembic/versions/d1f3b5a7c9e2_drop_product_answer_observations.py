"""删除产品回答观测表：回答校验层已拆除，观测数据不再保留。

Revision ID: d1f3b5a7c9e2
Revises: c8d2a4f6e1b7
Create Date: 2026-10-05 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "d1f3b5a7c9e2"
down_revision = "c8d2a4f6e1b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_product_answer_observations_observed_at")
    op.execute("DROP TABLE IF EXISTS product_answer_observations")


def downgrade() -> None:
    # 只恢复表结构，删除的观测数据无法恢复。
    op.create_table(
        "product_answer_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_path", sa.String(40), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason_category", sa.String(32), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column("product_tool_attempted", sa.Boolean(), nullable=False),
        sa.Column("product_result_types", sa.JSON(), nullable=False),
    )
    op.create_index("ix_product_answer_observations_observed_at", "product_answer_observations", ["observed_at"])
