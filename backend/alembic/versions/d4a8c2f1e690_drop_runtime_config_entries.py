"""运行时配置下线：配置值全部回到代码常量，删除 runtime_config_entries 及 Prompt bundle 治理遗留表。

Prompt 正文早已只来自代码仓库；prompt_bundle_* 三张表在 dev 上为空，代码不再读写。
表上的触发器随表删除，PostgreSQL 的触发器函数单独删除。
"""

from alembic import op

revision = "d4a8c2f1e690"
down_revision = "c7d2e9a4f613"
branch_labels = None
depends_on = None

_TABLES = (
    "runtime_config_entries",
    "prompt_bundle_hold_transitions",
    "prompt_bundle_hold_states",
    "prompt_bundle_engine_transitions",
)
_POSTGRESQL_FUNCTIONS = (
    "fusion_guard_prompt_engine",
    "fusion_guard_prompt_bundle_hold",
    "fusion_reject_prompt_hold_event_mutation",
)


def upgrade() -> None:
    for table in _TABLES:
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        for function in _POSTGRESQL_FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS {function}()")


def downgrade() -> None:
    raise RuntimeError("运行时配置已下线，配置值在代码常量中；代码回滚不执行本迁移降级")
