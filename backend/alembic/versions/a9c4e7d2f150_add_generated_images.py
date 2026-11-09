"""生图改存 image-service：新增 generated_images 记图属于哪段会话；首页示例图的来源改记图片编号。

原来生成的图转存在 Fusion 文件存储，首页示例图记的是那份文件的 ID；这些图随本次迁移一并删除，
旧 ID 不再对应任何图，改名时清空（示例图本身另存了一份，不受影响）。
"""

import sqlalchemy as sa

from alembic import op

revision = "a9c4e7d2f150"
down_revision = "e4b8d2a6c1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_images",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mime_type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_generated_images_conversation_id", "generated_images", ["conversation_id"])
    op.create_index("ix_generated_images_user_id", "generated_images", ["user_id"])
    op.execute(sa.text("UPDATE home_showcase_images SET source_file_id = NULL"))
    with op.batch_alter_table("home_showcase_images") as batch:
        batch.alter_column(
            "source_file_id",
            new_column_name="source_image_id",
            existing_type=sa.String(),
            type_=sa.String(length=64),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("home_showcase_images") as batch:
        batch.alter_column(
            "source_image_id",
            new_column_name="source_file_id",
            existing_type=sa.String(length=64),
            type_=sa.String(),
            existing_nullable=True,
        )
    op.drop_index("ix_generated_images_user_id", table_name="generated_images")
    op.drop_index("ix_generated_images_conversation_id", table_name="generated_images")
    op.drop_table("generated_images")
