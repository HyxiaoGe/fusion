"""首页生图示例卡片改由管理员维护的图片池。

Revision ID: b9d2e6a4c1f8
Revises: a7c3e9f1d204
Create Date: 2026-10-24 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "b9d2e6a4c1f8"
down_revision = "a7c3e9f1d204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "home_showcase_images",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("object_position", sa.String(length=16), nullable=True),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("home_showcase_images")
