"""删除早期功能遗留、代码早已不读写的孤儿表。

这些表没有 ORM 模型、没有迁移创建它们，只存在于老环境（dev）：旧记忆系统、旧模型来源/凭据、
旧 Prompt 模板、旧设置、热点/RSS/定时任务。新环境里不存在，所以用 IF EXISTS；按外键先删子表。
"""

from alembic import op

revision = "a3e9f7b2c514"
down_revision = "d4a8c2f1e690"
branch_labels = None
depends_on = None

_TABLES = (
    "topic_cluster_items",
    "daily_topic_digests",
    "hot_topics",
    "model_credentials",
    "model_sources",
    "providers",
    "memories",
    "prompt_templates",
    "rss_sources",
    "scheduled_tasks",
    "settings",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    raise RuntimeError("遗留孤儿表已删除且无代码使用；代码回滚不执行本迁移降级")
