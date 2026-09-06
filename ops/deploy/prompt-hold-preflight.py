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
            raise ValueError(
                "hold schema 启用后目标必须保持 apply，避免检查后进入 held 的竞态"
            )
        stage = _engine_stage(session)
        required = {
            "legacy": {"none"},
            "bridge": {"none", "jinja2"},
            "jinja2": {"jinja2"},
        }[stage]
        verify_engine_reader_profiles(required)
        if stage != "legacy" and not settings.PROMPT_P0_BASELINE_ATTESTED:
            raise ValueError("引擎迁移后目标必须保持 P0 attestation")
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
    try:
        from app.services.prompt_effective_map import assert_payload_p0_gate
    except ImportError:
        assert_p0_transition_gate(contents)
    else:
        assert_payload_p0_gate(row.payload)
    frozen = freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
    if frozen.source_kind != "prompthub_lkg" or frozen.source_revision != row.version:
        raise ValueError("目标镜像真实冻结入口未消费当前完整 v2")
    if {item.key: item.content for item in frozen.templates} != contents:
        raise ValueError("目标镜像冻结正文与完整 v2 原字节不一致")
    for item in frozen.templates:
        expected = row.payload["prompts"][item.key]
        if getattr(item, "template_engine", "none") != expected["template_engine"]:
            raise ValueError("目标镜像冻结引擎与完整 v2 不一致")
    return {
        "state": state,
        "source_kind": frozen.source_kind,
        "revision": frozen.source_revision,
    }


def _engine_stage(session):
    if not inspect(session.get_bind()).has_table("prompt_bundle_engine_transitions"):
        from app.db import models

        if hasattr(models, "PromptBundleEngineTransition"):
            raise ValueError("目标引擎桥接代码缺少必需迁移")
        return "legacy"
    from app.core.config import settings

    stages = set(
        session.execute(
            text(
                "SELECT stage FROM prompt_bundle_engine_transitions WHERE project_slug=:project"
            ),
            {"project": settings.PROMPTHUB_PROJECT_SLUG},
        ).scalars()
    )
    if stages - {"bridge", "jinja2"} or ("jinja2" in stages and "bridge" not in stages):
        raise ValueError("持久引擎阶段历史无效")
    return (
        "jinja2" if "jinja2" in stages else "bridge" if "bridge" in stages else "legacy"
    )


def verify_engine_reader_profiles(required):
    """执行目标校验器、冻结结构和真实消费方；不用版本字符串替代能力。"""
    from app.ai.prompts.prompt_manager import prompt_manager
    from app.core.prompt_bundle import validate_published_bundle
    from app.core.prompt_snapshot import build_bundle_snapshot, use_prompt_snapshot

    for engine in sorted(required):
        bundle, expected, values = _engine_probe_material(engine)
        payload = validate_published_bundle(bundle)
        defaults = {key: item["content"] for key, item in payload["prompts"].items()}
        snapshot = build_bundle_snapshot(
            defaults, payload=payload, classifier_prompt=""
        )
        with use_prompt_snapshot(snapshot):
            for key, body in expected.items():
                if values[key]:
                    actual = prompt_manager.format_prompt(key, **values[key])
                else:
                    actual = snapshot.resolve(key)[0]
                if actual.encode("utf-8") != body.encode("utf-8"):
                    raise ValueError("目标实际消费路径不满足完整引擎契约")
        if engine == "jinja2" and any(
            getattr(item, "template_engine", None) != engine
            for item in snapshot.templates
        ):
            raise ValueError("目标未冻结 Jinja2 元数据")


def _engine_probe_material(engine):
    import hashlib
    from types import SimpleNamespace

    from app.core.config import settings

    # 这是发布器自带的独立完整契约，不能借目标 renderer 产生期望值。
    variables = {
        "app_identity": (),
        "tool_usage_contract": (),
        "no_tool_network_boundary": (),
        "no_vision_file_boundary": (),
        "url_read_tool_description": (),
        "limit_summary": (),
        "continuation_system": (),
        "generate_title": ("content",),
        "generate_suggested_questions": ("content",),
        "file_analysis": ("query", "file_content"),
        "file_content_enhancement": ("query", "file_content"),
    }
    prompts, expected, values = [], {}, {}
    for key, names in variables.items():
        content = (
            "契约正文\n"
            if not names
            else "|".join(
                "{{ " + name + " }}" if engine == "jinja2" else "{" + name + "}"
                for name in names
            )
            + "\n"
        )
        values[key] = {name: "中文 <tag> {{原样}} 🙂\r\n" for name in names}
        expected[key] = "|".join(values[key].values()) + "\n" if names else content
        prompts.append(
            SimpleNamespace(
                slug=key.replace("_", "-"),
                content=content,
                variables=names,
                raw_variables=list(names),
                version="1.0.0",
                status="published",
                format="text",
                template_engine=engine,
                published_at=None,
            )
        )
    canonical = {
        "project_slug": settings.PROMPTHUB_PROJECT_SLUG,
        "prompts": [
            {
                "slug": item.slug,
                "version": item.version,
                "content_sha256": hashlib.sha256(
                    item.content.encode("utf-8")
                ).hexdigest(),
                "variables": item.raw_variables,
            }
            for item in sorted(prompts, key=lambda item: item.slug)
        ],
    }
    revision = hashlib.sha256(
        json.dumps(
            canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return (
        SimpleNamespace(
            project_slug=settings.PROMPTHUB_PROJECT_SLUG,
            revision=revision,
            prompts=tuple(prompts),
        ),
        expected,
        values,
    )


if __name__ == "__main__":
    from app.db.database import SessionLocal

    prompt_hold_preflight_result = verify_prompt_hold_target(SessionLocal)
    print(json.dumps(prompt_hold_preflight_result, ensure_ascii=False))
