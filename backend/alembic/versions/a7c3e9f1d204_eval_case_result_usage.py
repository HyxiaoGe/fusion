"""回放评测：判分结果记录 token 用量与估算成本。

Revision ID: a7c3e9f1d204
Revises: e4a9c2f7b318
Create Date: 2026-10-19 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a7c3e9f1d204"
down_revision = "e4a9c2f7b318"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_case_results", sa.Column("usage", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_case_results", "usage")
