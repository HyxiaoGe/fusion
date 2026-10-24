"""首页示例图记录来源生成图，挑选列表据此标出已在图片池。

Revision ID: c4e8a2d6f913
Revises: b9d2e6a4c1f8
Create Date: 2026-10-24 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e8a2d6f913"
down_revision = "b9d2e6a4c1f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("home_showcase_images", sa.Column("source_file_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("home_showcase_images", "source_file_id")
