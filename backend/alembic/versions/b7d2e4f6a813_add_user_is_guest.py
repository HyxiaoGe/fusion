"""用户表增加 is_guest，区分游客身份与登录用户。

Revision ID: b7d2e4f6a813
Revises: a3e9f7b2c514
Create Date: 2026-10-12 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "b7d2e4f6a813"
down_revision = "a3e9f7b2c514"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("users", "is_guest")
