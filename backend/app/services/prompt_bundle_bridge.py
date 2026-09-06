"""部署专用 v1 → v2 桥接；普通同步和 Run 热路径不得调用旧格式读取。"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import settings
from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload
from app.core.prompt_bundle_integrity import PROMPT_BUNDLE_NAMESPACE, compute_local_payload_checksum
from app.core.runtime_config import SessionFactory
from app.db.database import SessionLocal
from app.db.models import RuntimeConfigEntry, get_china_time
from app.services.prompt_effective_map import assert_payload_p0_gate
from app.services.prompthub_sync_service import (
    _acquire_advisory_lock,
    _clear_prompt_caches,
    _load_bundle_rows,
    _persist_locked,
)

_LEGACY_KEY = "fusion"
_RECEIPT_KEY = "fusion:v2:bridge"


class PromptBundleBridgeError(ValueError):
    """桥接前置条件不成立；保留旧服务与数据库状态。"""


def seed_verified_bundle(
    bundle: Any,
    *,
    baseline: dict[str, str],
    expected_legacy_revision: str,
    actor: str,
    reason: str,
    session_factory: SessionFactory = SessionLocal,
) -> dict[str, Any]:
    """从真实 published 材料新建 v2，v1 仅用于核对原有效正文，绝不补字段。"""
    _require_operator(actor, reason)
    if not settings.PROMPT_P0_BASELINE_ATTESTED:
        raise PromptBundleBridgeError("v2 桥接要求已完成 P0 基线 attestation")
    payload = validate_published_bundle(bundle)
    contents = {key: item["content"] for key, item in payload["prompts"].items()}
    _assert_same_contents(contents, baseline)
    assert_payload_p0_gate(payload)
    with session_factory() as session:
        _acquire_advisory_lock(session)
        legacy = _require_legacy_baseline(session, expected_legacy_revision, baseline)
        rows = _load_bundle_rows(session)
        if rows:
            return _require_seed_idempotence(rows, payload)
        _persist_locked(session, rows, payload, mode="apply")
        receipt_id = _append_receipt(
            session,
            "seeded",
            actor,
            reason,
            payload["revision"],
            legacy_revision=legacy.version,
            legacy_payload_checksum=compute_local_payload_checksum(legacy.payload),
            local_payload_checksum=payload["local_payload_checksum"],
        )
        session.commit()
    _clear_prompt_caches()
    return {"status": "seeded", "revision": payload["revision"], "receipt_id": receipt_id}


def retire_legacy_bundle(
    *,
    expected_v2_revision: str,
    actor: str,
    reason: str,
    session_factory: SessionFactory = SessionLocal,
) -> dict[str, Any]:
    """发布器证明全部旧 worker 已退出后调用；仅停用，不更新或删除旧 payload。"""
    _require_operator(actor, reason)
    with session_factory() as session:
        _acquire_advisory_lock(session)
        active = [row for row in _load_bundle_rows(session) if row.is_active]
        if (
            len(active) != 1
            or active[0].version != expected_v2_revision
            or not validate_stored_bundle_payload(active[0].payload)
        ):
            raise PromptBundleBridgeError("不能停用 v1：目标 v2 active 尚未验证")
        assert_payload_p0_gate(active[0].payload)
        legacy = _load_legacy_active_rows(session)
        if any(row.payload.get("schema_version") != 1 for row in legacy):
            raise PromptBundleBridgeError("旧存储键中存在未知 schema，拒绝自动停用")
        if not legacy:
            return {"status": "already_retired", "retired": 0}
        for row in legacy:
            row.is_active = False
        receipt_id = _append_receipt(
            session,
            "retired",
            actor,
            reason,
            expected_v2_revision,
            legacy_revisions=[row.version for row in legacy],
        )
        session.commit()
    return {"status": "retired", "retired": len(legacy), "receipt_id": receipt_id}


def _require_legacy_baseline(session, expected_revision: str, baseline: dict[str, str]):
    rows = _load_legacy_active_rows(session)
    if len(rows) != 1 or rows[0].version != expected_revision:
        raise PromptBundleBridgeError("旧 active 身份与部署前抓取不一致，必须重新抓取基线")
    row = rows[0]
    payload = row.payload
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("project_slug") != settings.PROMPTHUB_PROJECT_SLUG
        or payload.get("revision") != row.version
        or not isinstance(payload.get("prompts"), dict)
    ):
        raise PromptBundleBridgeError("旧 active 不是可比对的 v1 完整包")
    _assert_same_contents({key: item.get("content") for key, item in payload["prompts"].items()}, baseline)
    return row


def _load_legacy_active_rows(session):
    return (
        session.query(RuntimeConfigEntry)
        .filter_by(namespace=PROMPT_BUNDLE_NAMESPACE, key=_LEGACY_KEY, is_active=True)
        .filter(RuntimeConfigEntry.payload["project_slug"].as_string() == settings.PROMPTHUB_PROJECT_SLUG)
        .all()
    )


def _require_seed_idempotence(rows, payload):
    active = [row for row in rows if row.is_active]
    if len(active) != 1 or active[0].version != payload["revision"] or active[0].payload != payload:
        raise PromptBundleBridgeError("v2 已有不同状态；桥接不覆盖、不修复现有行")
    if not validate_stored_bundle_payload(active[0].payload):
        raise PromptBundleBridgeError("既有 v2 已损坏，桥接不能作为修复入口")
    return {"status": "already_seeded", "revision": payload["revision"]}


def _assert_same_contents(candidate, baseline):
    if not isinstance(baseline, dict) or set(candidate) != set(baseline):
        raise PromptBundleBridgeError("桥接基线必须完整覆盖 catalog")
    if any(
        not isinstance(candidate[key], str)
        or not isinstance(baseline[key], str)
        or candidate[key].encode("utf-8") != baseline[key].encode("utf-8")
        for key in baseline
    ):
        raise PromptBundleBridgeError("桥接前后 effective map 不是逐 key UTF-8 字节相等")


def _require_operator(actor, reason):
    if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
        raise PromptBundleBridgeError("桥接操作必须记录操作者与原因")


def _append_receipt(session, action, actor, reason, revision, **details):
    receipt_id = str(uuid.uuid4())
    row = RuntimeConfigEntry(
        id=receipt_id,
        namespace=PROMPT_BUNDLE_NAMESPACE,
        key=_RECEIPT_KEY,
        version=receipt_id,
        payload={
            "action": action,
            "actor": actor,
            "reason": reason,
            "revision": revision,
            "created_at": get_china_time().isoformat(),
            **details,
        },
        is_active=False,
        description="部署桥接追加式回执，不是可消费的 Prompt bundle",
    )
    session.add(row)
    return receipt_id
