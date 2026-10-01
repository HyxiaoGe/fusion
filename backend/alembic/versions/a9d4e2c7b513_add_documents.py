"""增加交付物文档与版本表。

Revision ID: a9d4e2c7b513
Revises: c4e8a2f1d935
Create Date: 2026-10-02 Asia/Shanghai
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a9d4e2c7b513"
down_revision = "c4e8a2f1d935"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _empty_json_array():
    return sa.text("'[]'::jsonb") if op.get_bind().dialect.name == "postgresql" else sa.text("'[]'")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default="markdown"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("format IN ('markdown')", name="ck_documents_format"),
        sa.CheckConstraint("current_version >= 1", name="ck_documents_current_version"),
    )
    op.create_index("ix_documents_conversation_created", "documents", ["conversation_id", "created_at"])
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.String(500), nullable=True),
        sa.Column("sources", _json_type(), nullable=False, server_default=_empty_json_array()),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        sa.CheckConstraint("version >= 1", name="ck_document_versions_version"),
    )


def downgrade() -> None:
    op.drop_table("document_versions")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_index("ix_documents_conversation_created", table_name="documents")
    op.drop_table("documents")
