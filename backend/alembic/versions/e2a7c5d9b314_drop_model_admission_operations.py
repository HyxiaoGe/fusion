"""模型准入事务已由 scripts/model_onboard.py 取代，删除准入操作表。

Revision ID: e2a7c5d9b314
Revises: a9d4e2c7b513
Create Date: 2026-10-04 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e2a7c5d9b314"
down_revision = "a9d4e2c7b513"
branch_labels = None
depends_on = None

_TABLE = "model_admission_operations"


def upgrade() -> None:
    op.drop_table(_TABLE)


def downgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(64), nullable=False),
        sa.Column("governance_run_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token_hash", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("catalog_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "candidate_fingerprint", "governance_run_id", name="uq_model_admission_operation_candidate_run"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_model_admission_operations_status",
        ),
    )
    op.create_index("ix_model_admission_operations_status_created", _TABLE, ["status", "created_at", "id"])
    op.create_index(
        "uq_model_admission_operations_single_running",
        _TABLE,
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
