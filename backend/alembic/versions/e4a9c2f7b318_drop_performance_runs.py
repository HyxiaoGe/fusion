"""删除压测记录表 performance_runs：管理中心不再导入和展示压测结果。

Revision ID: e4a9c2f7b318
Revises: d8f2b6c4e135
Create Date: 2026-10-14 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e4a9c2f7b318"
down_revision = "d8f2b6c4e135"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_performance_runs_environment_created_id", table_name="performance_runs")
    op.drop_index("ix_performance_runs_created_id", table_name="performance_runs")
    op.drop_table("performance_runs")


def downgrade():
    op.create_table(
        "performance_runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("environment", sa.String(length=30), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("safe_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("imported_by_user_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_performance_runs_created_id", "performance_runs", ["created_at", "run_id"])
    op.create_index(
        "ix_performance_runs_environment_created_id",
        "performance_runs",
        ["environment", "created_at", "run_id"],
    )
