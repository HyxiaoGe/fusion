"""用户记忆：user_memories 表与 users.memory_enabled 开关（默认开启）。

Revision ID: c5e1a7d3f924
Revises: b7d2e4f6a813
Create Date: 2026-10-14 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "c5e1a7d3f924"
down_revision = "b7d2e4f6a813"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "user_memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "source_conversation_id",
            sa.String(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_memories_user_created", "user_memories", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_user_memories_user_created", table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_column("users", "memory_enabled")
