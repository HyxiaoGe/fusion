"""增加持久 Prompt hold、追加审计和旧 v2 写入者的数据库保护。

Revision ID: e4b6c9d2a701
Revises: c1e5b7a9d204
Create Date: 2026-09-06 15:30:00 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "e4b6c9d2a701"
down_revision = "c1e5b7a9d204"
branch_labels = None
depends_on = None
_PROMPT_LOCK_ID = 0x465553494F4E5048


def upgrade() -> None:
    _create_state_table()
    _create_transition_table()
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_postgresql_guards()
    elif dialect == "sqlite":
        _install_sqlite_guards()
    else:
        raise RuntimeError("该数据库尚未实现持久 hold 写入保护")


def _create_state_table():
    op.create_table(
        "prompt_bundle_hold_states",
        sa.Column("project_slug", sa.String(120), nullable=False),
        sa.Column("catalog", sa.String(120), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("target_revision", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_slug", "catalog"),
        sa.CheckConstraint("state IN ('following', 'held')", name="ck_prompt_hold_state"),
        sa.CheckConstraint(
            "(state = 'held' AND target_revision IS NOT NULL) OR (state = 'following' AND target_revision IS NULL)",
            name="ck_prompt_hold_target",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_prompt_hold_generation"),
    )


def _create_transition_table():
    op.create_table(
        "prompt_bundle_hold_transitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_slug", sa.String(120), nullable=False),
        sa.Column("catalog", sa.String(120), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_revision", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_slug", "catalog", "generation", name="uq_prompt_hold_transition_generation"),
        sa.CheckConstraint("action IN ('entered', 'released')", name="ck_prompt_hold_transition_action"),
        sa.CheckConstraint("generation > 0", name="ck_prompt_hold_transition_generation"),
    )


def _install_postgresql_guards():
    op.execute(_POSTGRESQL_BUNDLE_GUARD)
    op.execute("""
CREATE TRIGGER prompt_bundle_hold_guard
BEFORE INSERT OR UPDATE OR DELETE ON runtime_config_entries
FOR EACH ROW EXECUTE FUNCTION fusion_guard_prompt_bundle_hold()
""")
    op.execute("""
CREATE FUNCTION fusion_reject_prompt_hold_event_mutation() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
BEGIN
    RAISE EXCEPTION 'Prompt hold 审计只能追加' USING ERRCODE = '55000';
END;
$$
""")
    op.execute("""
CREATE TRIGGER prompt_hold_events_append_only
BEFORE UPDATE OR DELETE ON prompt_bundle_hold_transitions
FOR EACH ROW EXECUTE FUNCTION fusion_reject_prompt_hold_event_mutation()
""")


_POSTGRESQL_BUNDLE_GUARD = f"""
CREATE FUNCTION fusion_guard_prompt_bundle_hold() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE held_target text;
BEGIN
    IF (TG_OP <> 'INSERT' AND OLD.namespace = 'prompt_bundle' AND OLD.key = 'fusion:v2')
       OR (TG_OP <> 'DELETE' AND NEW.namespace = 'prompt_bundle' AND NEW.key = 'fusion:v2') THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'Prompt hold 写入要求 READ COMMITTED';
        END IF;
        PERFORM pg_advisory_xact_lock({_PROMPT_LOCK_ID});
    ELSE
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    IF TG_OP <> 'INSERT' AND OLD.namespace = 'prompt_bundle' AND OLD.key = 'fusion:v2' THEN
        SELECT target_revision INTO held_target FROM prompt_bundle_hold_states
        WHERE project_slug = OLD.payload->>'project_slug' AND catalog = 'fusion' AND state = 'held';
        IF held_target = OLD.version THEN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '禁止删除 held Prompt bundle';
            END IF;
            IF NOT NEW.is_active OR NEW.namespace <> OLD.namespace OR NEW.key <> OLD.key
               OR NEW.version <> OLD.version OR NEW.payload IS DISTINCT FROM OLD.payload THEN
                RAISE EXCEPTION '禁止改写或停用 held Prompt bundle';
            END IF;
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' AND NEW.namespace = 'prompt_bundle' AND NEW.key = 'fusion:v2' AND NEW.is_active THEN
        SELECT target_revision INTO held_target FROM prompt_bundle_hold_states
        WHERE project_slug = NEW.payload->>'project_slug' AND catalog = 'fusion' AND state = 'held';
        IF held_target IS NOT NULL AND NEW.version <> held_target THEN
            RAISE EXCEPTION 'held 期间禁止激活其他 Prompt revision';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$
"""


def _install_sqlite_guards():
    # SQLite 用于自动化事务验证；生产并发序列化由上面的 PostgreSQL 事务锁负责。
    held_old = """OLD.namespace = 'prompt_bundle' AND OLD.key = 'fusion:v2' AND EXISTS (
        SELECT 1 FROM prompt_bundle_hold_states WHERE catalog = 'fusion' AND state = 'held'
        AND project_slug = json_extract(OLD.payload, '$.project_slug') AND target_revision = OLD.version)"""
    _sqlite_trigger("prompt_hold_bundle_delete", "DELETE", "runtime_config_entries", held_old)
    _sqlite_trigger(
        "prompt_hold_bundle_update",
        "UPDATE",
        "runtime_config_entries",
        f"""{held_old} AND (
        NOT NEW.is_active OR NEW.namespace <> OLD.namespace OR NEW.key <> OLD.key
        OR NEW.version <> OLD.version OR NEW.payload IS NOT OLD.payload)""",
    )
    held_other = """NEW.namespace = 'prompt_bundle' AND NEW.key = 'fusion:v2' AND NEW.is_active AND EXISTS (
        SELECT 1 FROM prompt_bundle_hold_states WHERE catalog = 'fusion' AND state = 'held'
        AND project_slug = json_extract(NEW.payload, '$.project_slug') AND target_revision <> NEW.version)"""
    for action in ("INSERT", "UPDATE"):
        _sqlite_trigger(f"prompt_hold_bundle_other_{action.lower()}", action, "runtime_config_entries", held_other)
    for action in ("UPDATE", "DELETE"):
        _sqlite_trigger(f"prompt_hold_events_{action.lower()}", action, "prompt_bundle_hold_transitions", "1")


def _sqlite_trigger(name, action, table, condition):
    op.execute(f"""CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition}
BEGIN SELECT RAISE(ABORT, 'Prompt hold 数据库保护拒绝写入'); END""")


def downgrade() -> None:
    raise RuntimeError("Prompt hold 状态和审计必须保留；代码回滚不执行本迁移降级")
