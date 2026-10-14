"""记忆整理进度：memory_consolidation_cursors，每个对话已整理到的消息序号。

Revision ID: d8f2b6c4e135
Revises: c5e1a7d3f924
Create Date: 2026-10-14 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "d8f2b6c4e135"
down_revision = "c5e1a7d3f924"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_consolidation_cursors",
        sa.Column(
            "conversation_id",
            sa.String(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("processed_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("memory_consolidation_cursors")
