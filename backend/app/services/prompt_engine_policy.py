"""受管发布器专用的引擎阶段提升；不提供普通 runtime config 或 HTTP 写入口。"""

import copy

from app.core.config import settings
from app.core.prompt_bundle import validate_stored_bundle_payload
from app.core.prompt_template_engine import payload_template_engine
from app.db.database import SessionLocal
from app.db.models import PromptBundleEngineTransition
from app.db.prompt_bundle_hold_repository import load_prompt_bundle_hold
from app.services.prompt_effective_map import assert_payload_p0_gate
from app.services.prompthub_sync_service import _acquire_advisory_lock, _load_bundle_rows


def get_prompt_engine_stage(session):
    stages = {
        row.stage
        for row in session.query(PromptBundleEngineTransition)
        .filter_by(project_slug=settings.PROMPTHUB_PROJECT_SLUG)
        .all()
    }
    if stages - {"bridge", "jinja2"} or ("jinja2" in stages and "bridge" not in stages):
        raise ValueError("引擎迁移历史不完整")
    return "jinja2" if "jinja2" in stages else "bridge" if "bridge" in stages else "legacy"


def verify_final_prompt_engine_stage(*, session_factory=SessionLocal):
    """最终运行时启动必须在已完成的持久 Jinja2 阶段，不能跳过桥接。"""
    with session_factory() as session:
        _acquire_advisory_lock(session)
        if get_prompt_engine_stage(session) != "jinja2":
            raise ValueError("最终 Jinja2 运行时要求先完成独立 bridge 与持久阶段收口")


def advance_prompt_engine_policy(stage, *, expected_revision, actor, reason, evidence, session_factory=SessionLocal):
    """在发布串行协调范围内调用，证据由真实 worker 与回滚镜像检查生成。"""
    if stage not in {"bridge", "jinja2"} or settings.PROMPTHUB_SYNC_MODE != "apply":
        raise ValueError("引擎提升要求 apply 与明确目标阶段")
    if not settings.PROMPT_P0_BASELINE_ATTESTED:
        raise ValueError("引擎提升必须先完成 P0 attestation")
    if (
        not isinstance(actor, str)
        or not 1 <= len(actor.strip()) <= 120
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("必须记录合法操作者和原因")
    with session_factory() as session:
        _acquire_advisory_lock(session)
        current = get_prompt_engine_stage(session)
        payload = _require_active(session, expected_revision)
        if current == stage:
            return {"stage": current, "idempotent": True, "revision": expected_revision}
        if {"legacy": "bridge", "bridge": "jinja2"}.get(current) != stage:
            raise ValueError("引擎阶段只能 legacy → bridge → jinja2，不能跳过或降级")
        if stage == "jinja2" and payload_template_engine(payload) != "jinja2":
            raise ValueError("收口前必须先激活完整 Jinja2；不能自动解除旧 hold")
        session.add(
            PromptBundleEngineTransition(
                project_slug=settings.PROMPTHUB_PROJECT_SLUG,
                stage=stage,
                revision=expected_revision,
                actor=actor,
                reason=reason.strip(),
                evidence=copy.deepcopy(evidence),
            )
        )
        session.commit()
    return {"stage": stage, "idempotent": False, "revision": expected_revision}


def _require_active(session, expected_revision):
    active = [row for row in _load_bundle_rows(session) if row.is_active]
    if (
        len(active) != 1
        or active[0].version != expected_revision
        or not validate_stored_bundle_payload(active[0].payload)
    ):
        raise ValueError("当前完整 active 已变化或损坏，须重新核验所有消费者")
    if active[0].payload["revision"] != expected_revision:
        raise ValueError("物理行与来源 revision 不一致")
    hold = load_prompt_bundle_hold(session, settings.PROMPTHUB_PROJECT_SLUG, "fusion")
    if hold is not None and hold.state == "held" and hold.target_revision != expected_revision:
        raise ValueError("held target 与当前完整 active 不一致，不能提升引擎阶段")
    assert_payload_p0_gate(active[0].payload)
    return active[0].payload
