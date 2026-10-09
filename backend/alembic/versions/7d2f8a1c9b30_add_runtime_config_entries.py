"""add runtime config entries

Revision ID: 7d2f8a1c9b30
Revises: 9c1f2a3b4d5e
Create Date: 2026-07-02
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7d2f8a1c9b30"
down_revision: Union[str, Sequence[str], None] = "9c1f2a3b4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_config_entries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("namespace", sa.String(length=80), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "key", "version", name="uq_runtime_config_namespace_key_version"),
    )
    op.create_index(
        "ix_runtime_config_active_lookup", "runtime_config_entries", ["namespace", "key", "is_active", "updated_at"]
    )
    op.create_index(op.f("ix_runtime_config_entries_created_at"), "runtime_config_entries", ["created_at"])
    op.create_index(op.f("ix_runtime_config_entries_is_active"), "runtime_config_entries", ["is_active"])
    op.create_index(op.f("ix_runtime_config_entries_key"), "runtime_config_entries", ["key"])
    op.create_index(op.f("ix_runtime_config_entries_namespace"), "runtime_config_entries", ["namespace"])
    op.create_index(op.f("ix_runtime_config_entries_updated_at"), "runtime_config_entries", ["updated_at"])
    # 原先在此 seed 代码默认值；运行时配置已下线（见 d4a8c2f1e690），表会被删除，不再写入。


def downgrade() -> None:
    op.drop_index(op.f("ix_runtime_config_entries_updated_at"), table_name="runtime_config_entries")
    op.drop_index(op.f("ix_runtime_config_entries_namespace"), table_name="runtime_config_entries")
    op.drop_index(op.f("ix_runtime_config_entries_key"), table_name="runtime_config_entries")
    op.drop_index(op.f("ix_runtime_config_entries_is_active"), table_name="runtime_config_entries")
    op.drop_index(op.f("ix_runtime_config_entries_created_at"), table_name="runtime_config_entries")
    op.drop_index("ix_runtime_config_active_lookup", table_name="runtime_config_entries")
    op.drop_table("runtime_config_entries")
