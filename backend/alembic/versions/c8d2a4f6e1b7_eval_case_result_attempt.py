"""回放评测：判分结果增加复核次数 attempt。

Revision ID: c8d2a4f6e1b7
Revises: b3f7e1a9c2d4
Create Date: 2026-10-04 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "c8d2a4f6e1b7"
down_revision = "b3f7e1a9c2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_case_results", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.drop_constraint("uq_eval_case_results_run_case_model", "eval_case_results", type_="unique")
    op.create_unique_constraint(
        "uq_eval_case_results_run_case_model_attempt",
        "eval_case_results",
        ["suite_run_id", "case_id", "model_id", "attempt"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM eval_case_results WHERE attempt > 1")
    op.drop_constraint("uq_eval_case_results_run_case_model_attempt", "eval_case_results", type_="unique")
    op.create_unique_constraint(
        "uq_eval_case_results_run_case_model",
        "eval_case_results",
        ["suite_run_id", "case_id", "model_id"],
    )
    op.drop_column("eval_case_results", "attempt")
