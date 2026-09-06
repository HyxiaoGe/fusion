"""由当前发布器注入目标镜像；只检查数据库与目标代码的真实冻结能力。"""

import json

from sqlalchemy import inspect, text


def verify_prompt_hold_target(session_factory):
    from app.core.config import settings

    with session_factory() as session:
        if not inspect(session.get_bind()).has_table("prompt_bundle_hold_states"):
            from app.db import models

            if hasattr(models, "PromptBundleHoldState"):
                raise ValueError("目标 hold 代码缺少必需数据库迁移，拒绝启动")
            return {"state": "schema_absent"}
        _require_v2_reader()
        if session.get_bind().dialect.name == "postgresql":
            isolation = session.execute(
                text("SELECT current_setting('transaction_isolation')")
            ).scalar_one()
            if isolation != "read committed":
                raise ValueError("Prompt hold 目标检查要求 READ COMMITTED")
            session.execute(text("SELECT pg_advisory_xact_lock(5068048530003611720)"))
        state = (
            session.execute(
                text(
                    "SELECT state, target_revision FROM prompt_bundle_hold_states WHERE project_slug=:project AND catalog='fusion'"
                ),
                {"project": settings.PROMPTHUB_PROJECT_SLUG},
            )
            .mappings()
            .first()
        )
        current_state = state["state"] if state is not None else "following"
        if current_state not in {"following", "held"}:
            raise ValueError("Prompt hold 持久状态无效")
        if settings.PROMPTHUB_SYNC_MODE != "apply":
            if current_state == "held":
                raise ValueError("held 期间目标必须保持 apply，不能静默使用代码默认值")
            return {"state": current_state, "mode": settings.PROMPTHUB_SYNC_MODE}
        return _verify_frozen(
            session, current_state, state["target_revision"] if state else None
        )


def _require_v2_reader():
    try:
        from app.core.prompt_bundle_integrity import PROMPT_BUNDLE_STORAGE_KEY
    except ModuleNotFoundError as exc:
        raise ValueError("hold schema 启用后目标镜像必须支持完整 v2") from exc
    if PROMPT_BUNDLE_STORAGE_KEY != "fusion:v2":
        raise ValueError("hold schema 启用后目标镜像必须读取 v2 存储键")


def _verify_frozen(session, state, target_revision):
    from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
    from app.core.config import settings
    from app.core.prompt_bundle import (
        freeze_prompt_bundle,
        validate_stored_bundle_payload,
    )
    from app.db.models import RuntimeConfigEntry
    from app.services.prompt_effective_map import assert_p0_transition_gate

    active = (
        session.query(RuntimeConfigEntry)
        .filter_by(namespace="prompt_bundle", key="fusion:v2", is_active=True)
        .all()
    )
    active = [
        row
        for row in active
        if isinstance(row.payload, dict)
        and row.payload.get("project_slug") == settings.PROMPTHUB_PROJECT_SLUG
    ]
    if len(active) != 1 or not validate_stored_bundle_payload(active[0].payload):
        raise ValueError("目标镜像不能校验当前完整 v2 active")
    row = active[0]
    if row.version != row.payload["revision"] or (
        state == "held" and row.version != target_revision
    ):
        raise ValueError("hold 与 active 的来源身份不一致")
    contents = {key: item["content"] for key, item in row.payload["prompts"].items()}
    assert_p0_transition_gate(contents)
    frozen = freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    if frozen.source_kind != "prompthub_lkg" or frozen.source_revision != row.version:
        raise ValueError("目标镜像真实冻结入口未消费当前完整 v2")
    if {item.key: item.content for item in frozen.templates} != contents:
        raise ValueError("目标镜像冻结正文与完整 v2 原字节不一致")
    return {
        "state": state,
        "source_kind": frozen.source_kind,
        "revision": frozen.source_revision,
    }


if __name__ == "__main__":
    from app.db.database import SessionLocal

    prompt_hold_preflight_result = verify_prompt_hold_target(SessionLocal)
    print(json.dumps(prompt_hold_preflight_result, ensure_ascii=False))
