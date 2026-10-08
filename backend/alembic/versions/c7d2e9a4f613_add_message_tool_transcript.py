"""保存每轮回答发给模型的工具调用与结果，供后续轮次原样回放。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c7d2e9a4f613"
down_revision = "b8e2f4a6d0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("tool_transcript", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("tool_transcript_cutoff_sequence", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "tool_transcript_cutoff_sequence")
    op.drop_column("messages", "tool_transcript")
