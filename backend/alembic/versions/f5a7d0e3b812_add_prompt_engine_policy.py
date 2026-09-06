"""增加单向引擎阶段审计与完整包激活保护。

Revision ID: f5a7d0e3b812
Revises: e4b6c9d2a701
Create Date: 2026-09-06 Asia/Shanghai
"""

import sqlalchemy as sa

from alembic import op

revision = "f5a7d0e3b812"
down_revision = "e4b6c9d2a701"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prompt_bundle_engine_transitions",
        sa.Column("project_slug", sa.String(120), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_slug", "stage"),
        sa.CheckConstraint("stage IN ('bridge', 'jinja2')", name="ck_prompt_engine_stage"),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _postgresql_guards()
    elif dialect == "sqlite":
        _sqlite_guards()
    else:
        raise RuntimeError("该数据库尚未实现引擎阶段保护")


def _postgresql_guards():
    op.execute("""
CREATE FUNCTION fusion_guard_prompt_engine() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE engine_stage text;
BEGIN
    IF NEW.namespace <> 'prompt_bundle' OR NEW.key <> 'fusion:v2' OR NOT NEW.is_active THEN RETURN NEW; END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Prompt 引擎策略要求 READ COMMITTED';
    END IF;
    PERFORM pg_advisory_xact_lock(5068048530003611720);
    SELECT CASE WHEN EXISTS (SELECT 1 FROM prompt_bundle_engine_transitions WHERE project_slug=NEW.payload->>'project_slug' AND stage='jinja2') THEN 'jinja2'
        WHEN EXISTS (SELECT 1 FROM prompt_bundle_engine_transitions WHERE project_slug=NEW.payload->>'project_slug' AND stage='bridge') THEN 'bridge' ELSE 'legacy' END INTO engine_stage;
    IF jsonb_typeof(NEW.payload::jsonb->'prompts') IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_each(NEW.payload::jsonb->'prompts')) <> 11
       OR EXISTS (SELECT 1 FROM jsonb_each(NEW.payload::jsonb->'prompts') AS p WHERE
           p.value->>'format' IS DISTINCT FROM 'text' OR
           (p.value->>'template_engine' IS DISTINCT FROM 'none' AND p.value->>'template_engine' IS DISTINCT FROM 'jinja2'))
       OR (SELECT count(DISTINCT value->>'template_engine') FROM jsonb_each(NEW.payload::jsonb->'prompts')) <> 1
       OR (engine_stage='legacy' AND NEW.payload::jsonb#>>'{prompts,generate_title,template_engine}' IS DISTINCT FROM 'none')
       OR (engine_stage='jinja2' AND NEW.payload::jsonb#>>'{prompts,generate_title,template_engine}' IS DISTINCT FROM 'jinja2') THEN
        RAISE EXCEPTION '完整包引擎不符合持久迁移阶段';
    END IF;
    RETURN NEW;
END;
$$
""")
    op.execute("""CREATE TRIGGER prompt_bundle_engine_guard BEFORE INSERT OR UPDATE ON runtime_config_entries
FOR EACH ROW EXECUTE FUNCTION fusion_guard_prompt_engine()""")
    op.execute("""CREATE TRIGGER prompt_engine_events_append_only BEFORE UPDATE OR DELETE ON prompt_bundle_engine_transitions
FOR EACH ROW EXECUTE FUNCTION fusion_reject_prompt_hold_event_mutation()""")


def _sqlite_guards():
    stage = """CASE WHEN EXISTS (SELECT 1 FROM prompt_bundle_engine_transitions WHERE project_slug=json_extract(NEW.payload,'$.project_slug') AND stage='jinja2') THEN 'jinja2'
        WHEN EXISTS (SELECT 1 FROM prompt_bundle_engine_transitions WHERE project_slug=json_extract(NEW.payload,'$.project_slug') AND stage='bridge') THEN 'bridge' ELSE 'legacy' END"""
    invalid = f"""NEW.namespace='prompt_bundle' AND NEW.key='fusion:v2' AND NEW.is_active AND (
        json_type(NEW.payload,'$.prompts') IS NOT 'object' OR
        (SELECT count(*) FROM json_each(NEW.payload,'$.prompts')) <> 11 OR
        EXISTS (SELECT 1 FROM json_each(NEW.payload,'$.prompts') WHERE
            json_extract(value,'$.format') IS NOT 'text' OR
            (json_extract(value,'$.template_engine') IS NOT 'none' AND json_extract(value,'$.template_engine') IS NOT 'jinja2')) OR
        (SELECT count(DISTINCT json_extract(value,'$.template_engine')) FROM json_each(NEW.payload,'$.prompts')) <> 1 OR
        (({stage})='legacy' AND json_extract(NEW.payload,'$.prompts.generate_title.template_engine') IS NOT 'none') OR
        (({stage})='jinja2' AND json_extract(NEW.payload,'$.prompts.generate_title.template_engine') IS NOT 'jinja2'))"""
    for action in ("INSERT", "UPDATE"):
        op.execute(f"""CREATE TRIGGER prompt_engine_bundle_{action.lower()} BEFORE {action} ON runtime_config_entries
WHEN {invalid} BEGIN SELECT RAISE(ABORT, '完整包引擎不符合持久迁移阶段'); END""")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER prompt_engine_events_{action.lower()} BEFORE {action} ON prompt_bundle_engine_transitions
BEGIN SELECT RAISE(ABORT, '引擎迁移审计只能追加'); END""")


def downgrade():
    raise RuntimeError("引擎策略和审计必须保留；代码回滚不降级迁移")
