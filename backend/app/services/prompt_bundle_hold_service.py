"""管理员本地回滚与解除 hold；所有转换和审计使用同一激活事务锁。"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.config import settings
from app.core.prompt_bundle import validate_stored_bundle_payload
from app.core.prompt_bundle_integrity import PROMPT_BUNDLE_LOGICAL_CATALOG
from app.core.runtime_config import SessionFactory
from app.db.database import SessionLocal
from app.db.models import PromptBundleHoldState, PromptBundleHoldTransition, get_china_time
from app.db.prompt_bundle_hold_repository import load_prompt_bundle_hold
from app.schemas.response import ApiException
from app.services.prompt_effective_map import assert_p0_transition_gate
from app.services.prompthub_sync_service import (
    _acquire_advisory_lock,
    _activate_row,
    _clear_prompt_caches,
    _load_bundle_rows,
)


def get_prompt_bundle_hold(*, session_factory: SessionFactory = SessionLocal) -> dict[str, Any]:
    """读取数据库当前状态，不使用同步器或 bundle 的进程缓存。"""
    with session_factory() as session:
        _acquire_advisory_lock(session)
        state = _load_state(session)
        rows = _load_bundle_rows(session)
        active = [row for row in rows if row.is_active and _valid_target(row)]
        return {**_state_view(state), "active_revision": active[0].version if len(active) == 1 else None}


def enter_prompt_bundle_hold(
    target_revision: str, *, actor: str, reason: str, session_factory: SessionFactory = SessionLocal
) -> dict[str, Any]:
    """原子激活完整 v2 历史版本并锁定；不依赖 PromptHub 或外部模型。"""
    _validate_operation(target_revision, actor, reason)
    with session_factory() as session:
        _acquire_advisory_lock(session)
        state = _load_state(session)
        rows = _load_bundle_rows(session)
        target = next((row for row in rows if row.version == target_revision), None)
        if target is None or not _valid_target(target):
            raise ApiException.conflict("回滚目标必须是当前 catalog 中校验通过的完整 v2")
        assert_p0_transition_gate({key: item["content"] for key, item in target.payload["prompts"].items()})
        if state is not None and state.state == "held":
            if state.target_revision != target_revision or [row.id for row in rows if row.is_active] != [target.id]:
                raise ApiException.conflict("已有不同或损坏的 hold；须先核验并解除")
            return {**_state_view(state), "idempotent": True}
        _activate_row(rows, target)
        session.flush()
        state = _transition(session, state, "entered", target_revision, actor, reason)
        result = {**_state_view(state), "idempotent": False}
        session.commit()
    _clear_prompt_caches()
    return result


def release_prompt_bundle_hold(
    target_revision: str, *, actor: str, reason: str, session_factory: SessionFactory = SessionLocal
) -> dict[str, Any]:
    """解除已核验目标的 hold；下次正常同步再跟随当时的完整 published 包。"""
    _validate_operation(target_revision, actor, reason)
    with session_factory() as session:
        _acquire_advisory_lock(session)
        state = _load_state(session)
        if state is None or state.state == "following":
            return {**_state_view(state), "idempotent": True}
        if state.target_revision != target_revision:
            raise ApiException.conflict("hold 目标已变化，拒绝陈旧解除请求")
        state = _transition(session, state, "released", target_revision, actor, reason)
        result = {**_state_view(state), "idempotent": False}
        session.commit()
    _clear_prompt_caches()
    return result


def _load_state(session):
    return load_prompt_bundle_hold(session, settings.PROMPTHUB_PROJECT_SLUG, PROMPT_BUNDLE_LOGICAL_CATALOG)


def _valid_target(row):
    return validate_stored_bundle_payload(row.payload) and row.version == row.payload["revision"]


def _transition(session, state, action, target_revision, actor, reason):
    if state is None:
        state = PromptBundleHoldState(
            project_slug=settings.PROMPTHUB_PROJECT_SLUG, catalog=PROMPT_BUNDLE_LOGICAL_CATALOG, generation=0
        )
        session.add(state)
    now = get_china_time()
    state.state = "held" if action == "entered" else "following"
    state.target_revision = target_revision if action == "entered" else None
    state.generation += 1
    state.updated_at = now
    session.add(
        PromptBundleHoldTransition(
            id=str(uuid.uuid4()),
            project_slug=state.project_slug,
            catalog=state.catalog,
            generation=state.generation,
            action=action,
            target_revision=target_revision,
            actor=actor.strip(),
            reason=reason.strip(),
            created_at=now,
        )
    )
    return state


def _state_view(state):
    return {
        "project_slug": settings.PROMPTHUB_PROJECT_SLUG,
        "catalog": PROMPT_BUNDLE_LOGICAL_CATALOG,
        "state": state.state if state is not None else "following",
        "target_revision": state.target_revision if state is not None else None,
        "generation": state.generation if state is not None else 0,
    }


def _validate_operation(target_revision, actor, reason):
    if not isinstance(target_revision, str) or re.fullmatch(r"[0-9a-f]{64}", target_revision) is None:
        raise ApiException.bad_request("目标 revision 必须是完整 SHA-256")
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 120:
        raise ApiException.bad_request("必须记录有效操作者")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ApiException.bad_request("必须记录 1 至 2000 字符的操作原因")
