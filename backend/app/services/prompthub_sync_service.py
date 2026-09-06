"""PromptHub bundle 的后台同步、LKG 持久化与只读诊断。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.core.prompt_bundle import (
    PromptBundleValidationError,
    clear_prompt_bundle_cache,
    validate_published_bundle,
    validate_stored_bundle_payload,
)
from app.core.prompt_bundle_integrity import (
    PROMPT_BUNDLE_LOGICAL_CATALOG,
    PROMPT_BUNDLE_NAMESPACE,
    PROMPT_BUNDLE_STORAGE_KEY,
    PromptBundleRevisionConflict,
)
from app.core.prompt_catalog import PROMPT_SPECS
from app.core.runtime_config import SessionFactory, clear_runtime_config_cache
from app.db.database import SessionLocal
from app.db.models import RuntimeConfigEntry
from app.db.prompt_bundle_hold_repository import load_prompt_bundle_hold
from app.services.external.prompthub_client import (
    PromptHubClientError,
    PromptHubPublishedBundleClient,
)
from app.services.prompt_effective_map import EffectiveBaselineMismatch, assert_payload_p0_gate
from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

SyncMode = Literal["disabled", "shadow", "apply"]

_ADVISORY_LOCK_ID = 0x465553494F4E5048
_DIAGNOSTICS: dict[str, Any] = {
    "mode": settings.PROMPTHUB_SYNC_MODE,
    "status": "never_run",
    "last_attempt_at": None,
    "last_success_at": None,
    "revision": None,
    "changed_prompt_keys": [],
    "last_error": None,
}


async def sync_prompthub_bundle(
    *,
    mode: str | None = None,
    client: PromptHubPublishedBundleClient | Any | None = None,
    session_factory: SessionFactory = SessionLocal,
) -> dict[str, Any]:
    """同步完整 bundle；错误只更新诊断，不改变已激活 LKG。"""

    effective_mode = mode or settings.PROMPTHUB_SYNC_MODE
    attempted_at = _utc_now()
    if effective_mode == "disabled":
        result = _base_result(effective_mode, "disabled", attempted_at)
        _update_diagnostics(result)
        return result
    if effective_mode not in {"shadow", "apply"}:
        return _record_error(effective_mode, attempted_at, "PROMPTHUB_SYNC_MODE 无效")

    try:
        effective_client = client or _build_client()
        bundle = await effective_client.fetch_published_bundle()
        payload = validate_published_bundle(bundle)
        persist_result = await asyncio.to_thread(
            _persist_bundle,
            payload,
            mode=effective_mode,
            session_factory=session_factory,
        )
    except PromptBundleRevisionConflict as exc:
        return _record_error(effective_mode, attempted_at, str(exc), status="revision_conflict")
    except (PromptHubClientError, PromptBundleValidationError, EffectiveBaselineMismatch, ValueError) as exc:
        return _record_error(effective_mode, attempted_at, str(exc))
    except Exception:
        logger.exception("PromptHub bundle 同步发生未预期错误")
        return _record_error(effective_mode, attempted_at, "同步发生未预期错误")

    result = {
        **_base_result(effective_mode, "success", attempted_at),
        "last_success_at": _utc_now(),
        "revision": payload["revision"],
        **persist_result,
        "last_error": None,
    }
    _update_diagnostics(result)
    _log_sync_result(result)
    return copy.deepcopy(result)


def _log_sync_result(result):
    if result["hold_state"] == "held" and result["held_revision"] != result["revision"]:
        logger.warning(
            "Prompt hold 保持本地 revision=%s，published revision=%s 仅作校验",
            result["held_revision"],
            result["revision"],
        )
    logger.info(
        "PromptHub bundle 同步成功: mode=%s revision=%s changed=%s idempotent=%s",
        result["mode"],
        result["revision"],
        len(result["changed_prompt_keys"]),
        result["idempotent"],
    )


async def run_prompthub_sync_best_effort() -> dict[str, Any]:
    """供 startup/scheduler 调用，任何失败都不影响应用可用性。"""

    try:
        return await sync_prompthub_bundle()
    except Exception:
        logger.exception("PromptHub best-effort 同步失败")
        return _record_error(settings.PROMPTHUB_SYNC_MODE, _utc_now(), "同步发生未预期错误")


def get_prompthub_sync_diagnostics() -> dict[str, Any]:
    """返回不含凭证和 Prompt 内容的 admin 只读诊断。"""

    return copy.deepcopy(_DIAGNOSTICS)


def _build_client() -> PromptHubPublishedBundleClient:
    if not settings.PROMPTHUB_BASE_URL or not settings.PROMPTHUB_API_KEY:
        raise ValueError("PromptHub 地址或服务凭证未配置")
    return PromptHubPublishedBundleClient(
        base_url=settings.PROMPTHUB_BASE_URL,
        api_key=settings.PROMPTHUB_API_KEY,
        project_slug=settings.PROMPTHUB_PROJECT_SLUG,
        timeout_seconds=settings.PROMPTHUB_REQUEST_TIMEOUT_SECONDS,
    )


def _build_shadow_diff(payload: dict[str, Any], rows: list[RuntimeConfigEntry]) -> dict[str, Any]:
    """在激活事务的锁内固定差异基准，避免同步期间基线漂移。"""
    active = next((row for row in rows if row.is_active and validate_stored_bundle_payload(row.payload)), None)
    baseline = active.payload["prompts"] if active is not None else {}
    checksums = {key: item["content_sha256"] for key, item in payload["prompts"].items()}
    return {
        "baseline_revision": active.version if active is not None else None,
        "changed_prompt_keys": sorted(
            key for key, checksum in checksums.items() if baseline.get(key, {}).get("content_sha256") != checksum
        ),
        "code_default_changed_prompt_keys": sorted(
            spec.key for spec in PROMPT_SPECS if checksums[spec.key] != _sha256(DEFAULT_PROMPT_TEMPLATES[spec.key])
        ),
    }


def _persist_bundle(
    payload: dict[str, Any],
    *,
    mode: str,
    session_factory: SessionFactory,
) -> dict[str, Any]:
    session: Session | None = None
    try:
        if mode == "apply":
            assert_payload_p0_gate(payload)
        if not validate_stored_bundle_payload(payload):
            raise PromptBundleValidationError("待持久化的 v2 Prompt bundle 无效")
        session = session_factory()
        _acquire_advisory_lock(session)
        rows = _load_bundle_rows(session)
        diff = _build_shadow_diff(payload, rows)
        result = _persist_locked(session, rows, payload, mode=mode)
        if not result["idempotent"]:
            session.commit()
            _clear_prompt_caches()
        return {**diff, **result}
    except IntegrityError:
        if session is not None:
            session.rollback()
        raise PromptBundleRevisionConflict("Prompt bundle revision 并发写入冲突") from None
    except Exception:
        if session is not None:
            session.rollback()
        raise
    finally:
        if session is not None:
            session.close()


def _persist_locked(
    session: Session,
    rows: list[RuntimeConfigEntry],
    payload: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    hold = load_prompt_bundle_hold(session, settings.PROMPTHUB_PROJECT_SLUG, PROMPT_BUNDLE_LOGICAL_CATALOG)
    held = hold is not None and hold.state == "held"
    active = [row for row in rows if row.is_active]
    active_revision = active[0].version if len(active) == 1 else None
    if held and (
        active_revision != hold.target_revision
        or not validate_stored_bundle_payload(active[0].payload)
        or active[0].payload["revision"] != hold.target_revision
    ):
        raise PromptBundleValidationError("hold 目标与有效 active 不一致，拒绝自动改变任何 active")
    persist_mode = "shadow" if held else mode
    if persist_mode == "apply":
        assert_payload_p0_gate(payload)
    result = _persist_candidate_locked(session, rows, payload, mode=persist_mode)
    return {
        **result,
        "hold_state": "held" if held else "following",
        "held_revision": hold.target_revision if held else None,
        "active_revision": payload["revision"] if result["active"] else active_revision,
    }


def _persist_candidate_locked(session, rows, payload, *, mode):
    existing = next((row for row in rows if row.version == payload["revision"]), None)
    if existing is not None:
        if not validate_stored_bundle_payload(existing.payload) or existing.payload != payload:
            raise PromptBundleRevisionConflict("同 revision 的本地 Prompt bundle 已损坏或内容不一致")
        if mode == "shadow" or existing.is_active:
            return {"idempotent": True, "active": bool(existing.is_active)}
        _activate_row(rows, existing)
        return {"idempotent": False, "active": True}

    row = RuntimeConfigEntry(
        id=str(uuid.uuid4()),
        namespace=PROMPT_BUNDLE_NAMESPACE,
        key=PROMPT_BUNDLE_STORAGE_KEY,
        version=payload["revision"],
        payload=copy.deepcopy(payload),
        is_active=mode == "apply",
        description="PromptHub 完整校验的 v2 LKG",
    )
    if mode == "apply":
        _activate_row(rows, row)
    session.add(row)
    return {"idempotent": False, "active": bool(row.is_active)}


def _load_bundle_rows(session: Session) -> list[RuntimeConfigEntry]:
    rows = (
        session.query(RuntimeConfigEntry)
        .filter(
            RuntimeConfigEntry.namespace == PROMPT_BUNDLE_NAMESPACE,
            RuntimeConfigEntry.key == PROMPT_BUNDLE_STORAGE_KEY,
        )
        .order_by(RuntimeConfigEntry.updated_at.desc(), RuntimeConfigEntry.created_at.desc())
        .all()
    )
    return [
        row
        for row in rows
        if getattr(row, "namespace", None) == PROMPT_BUNDLE_NAMESPACE
        and getattr(row, "key", None) == PROMPT_BUNDLE_STORAGE_KEY
        and isinstance(row.payload, dict)
        and row.payload.get("project_slug") == settings.PROMPTHUB_PROJECT_SLUG
    ]


def _activate_row(rows: list[RuntimeConfigEntry], target: RuntimeConfigEntry) -> None:
    for row in rows:
        row.is_active = row.id == target.id


def _acquire_advisory_lock(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        isolation = session.execute(text("SELECT current_setting('transaction_isolation')")).scalar_one()
        if isolation != "read committed":
            raise ValueError("Prompt 激活和 hold 转换要求 READ COMMITTED，拒绝陈旧事务快照")
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _ADVISORY_LOCK_ID},
        )


def _clear_prompt_caches() -> None:
    clear_runtime_config_cache()
    clear_prompt_bundle_cache()


def _record_error(mode: str, attempted_at: str, message: str, *, status: str = "error") -> dict[str, Any]:
    result = {
        **_base_result(mode, status, attempted_at),
        "last_success_at": _DIAGNOSTICS.get("last_success_at"),
        "revision": _DIAGNOSTICS.get("revision"),
        "changed_prompt_keys": copy.deepcopy(_DIAGNOSTICS.get("changed_prompt_keys", [])),
        "last_error": message,
    }
    _update_diagnostics(result)
    logger.warning("PromptHub bundle 同步失败: mode=%s error=%s", mode, message)
    return copy.deepcopy(result)


def _base_result(mode: str, status: str, attempted_at: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": status,
        "last_attempt_at": attempted_at,
        "last_success_at": _DIAGNOSTICS.get("last_success_at"),
        "revision": _DIAGNOSTICS.get("revision"),
        "changed_prompt_keys": [],
        "last_error": None,
    }


def _update_diagnostics(result: dict[str, Any]) -> None:
    _DIAGNOSTICS.update(copy.deepcopy(result))


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
