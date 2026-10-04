"""回放评测：评测运行与逐条判分结果。

Revision ID: b3f7e1a9c2d4
Revises: e2a7c5d9b314
Create Date: 2026-10-04 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b3f7e1a9c2d4"
down_revision = "e2a7c5d9b314"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_suite_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("git_sha", sa.String(40), nullable=True),
        sa.Column("suite_sha256", sa.String(64), nullable=False),
        sa.Column("models", postgresql.JSONB(), nullable=False),
        sa.Column("case_ids", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_eval_suite_runs_status"),
    )
    op.create_index("ix_eval_suite_runs_started", "eval_suite_runs", ["started_at"])
    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "suite_run_id",
            sa.String(),
            sa.ForeignKey("eval_suite_runs.id", ondelete="CASCADE", name="fk_eval_case_results_suite_run"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(100), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("trajectory", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('passed', 'failed', 'error')", name="ck_eval_case_results_status"),
        sa.UniqueConstraint("suite_run_id", "case_id", "model_id", name="uq_eval_case_results_run_case_model"),
    )
    op.create_index(
        "ix_eval_case_results_case_model_created", "eval_case_results", ["case_id", "model_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_eval_case_results_case_model_created", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("ix_eval_suite_runs_started", table_name="eval_suite_runs")
    op.drop_table("eval_suite_runs")
