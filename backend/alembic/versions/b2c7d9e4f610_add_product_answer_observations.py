"""独立留存产品回答观测，不迁移旧日志样本。

Revision ID: b2c7d9e4f610
Revises: f5a7d0e3b812
Create Date: 2026-09-20 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "b2c7d9e4f610"
down_revision = "f5a7d0e3b812"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_answer_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_path", sa.String(40), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason_category", sa.String(32), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column("repair_enabled", sa.Boolean(), nullable=False),
        sa.Column("repair_available", sa.Boolean(), nullable=False),
        sa.Column("repair_applied", sa.Boolean(), nullable=False),
        sa.Column("repair_reason_code", sa.String(64), nullable=False),
        sa.Column("product_tool_attempted", sa.Boolean(), nullable=False),
        sa.Column("product_result_types", sa.JSON(), nullable=False),
    )
    op.create_index("ix_product_answer_observations_observed_at", "product_answer_observations", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_product_answer_observations_observed_at", table_name="product_answer_observations")
    op.drop_table("product_answer_observations")
