"""Prompt bundle 校验、LKG 读取与旧 Runtime Config 降级。"""

from __future__ import annotations

import copy
import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.core.prompt_bundle_integrity import (
    PROMPT_BUNDLE_NAMESPACE,
    PROMPT_BUNDLE_SCHEMA_VERSION,
    PROMPT_BUNDLE_STORAGE_KEY,
    PromptBundleRevisionConflict,
    PromptBundleValidationError,
    compute_local_payload_checksum,
    compute_source_revision,
    normalize_variable_names,
)
from app.core.prompt_catalog import (
    CATALOG_VERSION_BY_ENGINE,
    DEFAULT_TEMPLATE_ENGINE,
    PRE_P0_CODE_ONLY_KEYS,
    PROMPT_SPEC_BY_KEY,
    PROMPT_SPEC_BY_SLUG,
    PROMPT_SPECS,
)
from app.core.prompt_snapshot import (
    PromptBundleSnapshot,
    build_bundle_snapshot,
    current_prompt_snapshot,
)
from app.core.prompt_snapshot import (
    use_prompt_snapshot as use_prompt_snapshot,
)
from app.core.prompt_template_engine import payload_template_engine, template_contract_is_valid
from app.db.database import SessionLocal
from app.db.models import RuntimeConfigEntry

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_CACHE_TTL_SECONDS = 60.0
_BUNDLE_CACHE: tuple[float, dict[str, Any] | None] | None = None


def freeze_prompt_bundle(defaults: Mapping[str, str], *, classifier_prompt: str = "") -> PromptBundleSnapshot:
    """分类前一次解析完整包；无有效 LKG 时只允许整包代码默认值。"""
    payload = get_active_prompt_bundle_payload()
    if payload is not None and not settings.PROMPT_P0_BASELINE_ATTESTED:
        if any(payload["prompts"][key]["content"] != defaults[key] for key in PRE_P0_CODE_ONLY_KEYS):
            raise ValueError("P0 过渡正文尚未与完整基线一致，不能冻结混合来源")
    return build_bundle_snapshot(defaults, payload=payload, classifier_prompt=classifier_prompt)


def clear_prompt_bundle_cache() -> None:
    global _BUNDLE_CACHE
    _BUNDLE_CACHE = None


def validate_published_bundle(bundle: Any) -> dict[str, Any]:
    """将 PromptHub bundle 校验为可持久化的完整 LKG payload。"""

    issues: list[str] = []
    if getattr(bundle, "project_slug", None) != settings.PROMPTHUB_PROJECT_SLUG:
        issues.append("project_slug 不匹配")
    revision = getattr(bundle, "revision", None)
    if not isinstance(revision, str) or _SHA256_PATTERN.fullmatch(revision) is None:
        issues.append("revision 必须是 64 位 SHA-256")

    raw_prompts = tuple(getattr(bundle, "prompts", ()))
    slugs = [getattr(prompt, "slug", None) for prompt in raw_prompts]
    if len(slugs) != len(set(slugs)):
        issues.append("Prompt slug 不能重复")
    expected_slugs = set(PROMPT_SPEC_BY_SLUG)
    actual_slugs = {slug for slug in slugs if isinstance(slug, str)}
    if actual_slugs != expected_slugs or len(raw_prompts) != len(PROMPT_SPECS):
        issues.append(f"bundle 必须恰好包含 {len(PROMPT_SPECS)} 个约定 Prompt")

    engines = {getattr(item, "template_engine", None) for item in raw_prompts}
    if len(engines) != 1 or not engines.issubset(CATALOG_VERSION_BY_ENGINE):
        raise PromptBundleValidationError("; ".join([*issues, "完整 Prompt bundle 必须使用同一个受支持的引擎"]))
    engine = next(iter(engines))
    validated_prompts: dict[str, dict[str, Any]] = {}
    for prompt in raw_prompts:
        slug = getattr(prompt, "slug", None)
        spec = PROMPT_SPEC_BY_SLUG.get(slug)
        if spec is None:
            continue
        prompt_issues, serialized = _validate_prompt_item(prompt, replace(spec, template_engine=engine))
        issues.extend(prompt_issues)
        if serialized is not None:
            validated_prompts[spec.key] = serialized

    if issues:
        raise PromptBundleValidationError("; ".join(issues))
    if compute_source_revision(bundle.project_slug, validated_prompts.values()) != revision:
        raise PromptBundleRevisionConflict("PromptHub source_revision 与原始发布材料不一致")
    payload = {
        "schema_version": PROMPT_BUNDLE_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION_BY_ENGINE[engine],
        "project_slug": bundle.project_slug,
        "revision": revision,
        "prompts": validated_prompts,
    }
    payload["local_payload_checksum"] = compute_local_payload_checksum(payload)
    return payload


def validate_stored_bundle_payload(payload: Any) -> bool:
    return diagnose_stored_bundle_payload(payload) == "valid"


def diagnose_stored_bundle_payload(payload: Any) -> str:
    """区分旧格式、catalog 漂移与完整性损坏；诊断不包含正文。"""
    if not isinstance(payload, dict):
        return "payload_invalid"
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != PROMPT_BUNDLE_SCHEMA_VERSION:
        return "schema_unsupported"
    if payload.get("catalog_version") not in CATALOG_VERSION_BY_ENGINE.values():
        return "catalog_mismatch"
    if payload.get("project_slug") != settings.PROMPTHUB_PROJECT_SLUG:
        return "project_mismatch"
    revision, prompts = payload.get("revision"), payload.get("prompts")
    if not isinstance(revision, str) or _SHA256_PATTERN.fullmatch(revision) is None:
        return "revision_invalid"
    if not isinstance(prompts, dict) or set(prompts) != set(PROMPT_SPEC_BY_KEY):
        return "catalog_mismatch"
    try:
        if payload.get("local_payload_checksum") != compute_local_payload_checksum(payload):
            return "local_checksum_mismatch"
        engine = payload_template_engine(payload)
        if not all(_stored_prompt_is_valid(key, prompts.get(key), engine) for key in PROMPT_SPEC_BY_KEY):
            return "prompt_contract_invalid"
        if revision != compute_source_revision(payload["project_slug"], prompts.values()):
            return "revision_conflict"
        return "valid"
    except (KeyError, TypeError, ValueError, AttributeError):
        return "payload_invalid"


def resolve_prompt_template(name: str, fallback: str) -> str:
    """按 active bundle(LKG) -> 代码默认值解析 Prompt。"""

    template, _metadata = resolve_prompt_template_with_metadata(name, fallback)
    return template


def resolve_prompt_template_with_metadata(name: str, fallback: str) -> tuple[str, dict[str, str | None]]:
    """解析 Prompt，并返回可安全写入 LLM 观测字段的版本信息。

    消费链严格为 active bundle(LKG) -> 代码默认值两级。catalog key 不再回退到
    legacy ``prompt_template`` 命名空间：那条路径允许单个 key 独立回落到独立的
    Runtime Config 行，会让同一个 Run 由 bundle 与多行 legacy 拼出，破坏
    「完整 bundle 原子切换」与单 Run 冻结。
    """

    frozen = current_prompt_snapshot()
    if frozen is not None:
        return frozen.resolve(name)

    if _is_pinned_during_p0_transition(name):
        # 过渡期：这些 key 钉在代码默认值上，与 P0 之前逐字节一致。
        # 因此候选代码可直接在 apply 模式部署，无需经过会改变其余 key 的 disabled 窗口。
        spec = PROMPT_SPEC_BY_KEY.get(name)
        return fallback, {
            "source": "code-default-p0-transition",
            "prompt_slug": spec.slug if spec is not None else name,
            "prompt_version": "code-default",
            "format": "text",
            "template_engine": DEFAULT_TEMPLATE_ENGINE,
            "prompt_revision": None,
        }

    if settings.PROMPTHUB_SYNC_MODE == "apply":
        bundle = _load_active_bundle_payload()
        resolved = _template_from_bundle_with_metadata(bundle, name)
        if resolved is not None:
            return resolved

    spec = PROMPT_SPEC_BY_KEY.get(name)
    return fallback, {
        "source": "code-default",
        "prompt_slug": spec.slug if spec is not None else name,
        "prompt_version": "code-default",
        "format": "text",
        "template_engine": DEFAULT_TEMPLATE_ENGINE,
        "prompt_revision": None,
    }


def get_active_prompt_bundle_revision() -> str | None:
    """返回当前实际参与热路径解析的 bundle revision。"""

    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return None
    bundle = _load_active_bundle_payload()
    if not isinstance(bundle, dict) or not validate_stored_bundle_payload(bundle):
        return None
    revision = bundle.get("revision")
    return revision if isinstance(revision, str) else None


def load_stored_active_bundle_payload() -> dict[str, Any] | None:
    """只读当前已激活并校验通过的 stored LKG，**不受 PROMPTHUB_SYNC_MODE 影响**。

    `get_active_prompt_bundle_payload()` 在非 apply 模式必然返回 None，因为它表达的是
    「热路径此刻是否在用 bundle」。而抓取部署前 effective map 要回答的是另一个问题：
    「库里实际存着哪一版 LKG」——迁移工具必须用本函数，否则在 disabled/shadow 下
    抓取会把已消费项误记成 legacy / 代码默认值。
    """

    bundle = _load_active_bundle_payload(use_cache=False)
    if not isinstance(bundle, dict) or not validate_stored_bundle_payload(bundle):
        return None
    return copy.deepcopy(bundle)


def get_active_prompt_bundle_payload() -> dict[str, Any] | None:
    """返回当前 apply 热路径使用的 bundle 副本，供只读治理诊断使用。"""

    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return None
    bundle = _load_active_bundle_payload()
    if not isinstance(bundle, dict) or not validate_stored_bundle_payload(bundle):
        return None
    return copy.deepcopy(bundle)


def _validate_prompt_item(prompt: Any, spec: Any) -> tuple[list[str], dict[str, Any] | None]:
    issues: list[str] = []
    prefix = spec.slug
    content = getattr(prompt, "content", None)
    variables = getattr(prompt, "variables", None)
    if getattr(prompt, "status", None) != "published":
        issues.append(f"{prefix}: status 必须为 published")
    if getattr(prompt, "format", None) != spec.format:
        issues.append(f"{prefix}: format 必须为 {spec.format}")
    if getattr(prompt, "template_engine", None) != spec.template_engine:
        issues.append(f"{prefix}: template_engine 必须为 {spec.template_engine}")
    if getattr(prompt, "published_at", None) is not None and not isinstance(prompt.published_at, str):
        issues.append(f"{prefix}: published_at 无效")
    if not isinstance(content, str) or not content.strip():
        issues.append(f"{prefix}: content 必须是非空字符串")
    elif not template_contract_is_valid(content, spec.variables, spec.template_engine):
        issues.append(f"{prefix}: content 占位符与 variables 不匹配")
    if (
        not isinstance(variables, tuple)
        or len(variables) != len(set(variables))
        or set(variables) != set(spec.variables)
    ):
        issues.append(f"{prefix}: variables 不匹配")
    raw_variables = getattr(prompt, "raw_variables", None)
    try:
        raw_names = normalize_variable_names(raw_variables)
        if raw_names != variables:
            issues.append(f"{prefix}: 原始 variables 与归一化名字不一致")
    except (TypeError, ValueError):
        issues.append(f"{prefix}: 原始 variables 无效")
    version = getattr(prompt, "version", None)
    if not isinstance(version, str) or not version:
        issues.append(f"{prefix}: version 无效")
    if issues:
        return issues, None
    return [], {
        "slug": spec.slug,
        "version": version,
        "content": content,
        "variables": list(spec.variables),
        "raw_variables": copy.deepcopy(raw_variables),
        "format": prompt.format,
        "template_engine": prompt.template_engine,
        "content_sha256": _sha256(content),
        "published_at": getattr(prompt, "published_at", None),
    }


def _stored_prompt_is_valid(key: str, prompt: Any, engine: str) -> bool:
    spec = replace(PROMPT_SPEC_BY_KEY[key], template_engine=engine)
    if not isinstance(prompt, dict):
        return False
    content = prompt.get("content")
    variables = prompt.get("variables")
    checksum = prompt.get("content_sha256")
    return (
        prompt.get("slug") == spec.slug
        and isinstance(prompt.get("version"), str)
        and bool(prompt.get("version"))
        and isinstance(content, str)
        and bool(content.strip())
        and template_contract_is_valid(content, spec.variables, spec.template_engine)
        and isinstance(variables, list)
        and set(variables) == set(spec.variables)
        and len(variables) == len(spec.variables)
        and set(normalize_variable_names(prompt.get("raw_variables"))) == set(spec.variables)
        and len(normalize_variable_names(prompt.get("raw_variables"))) == len(spec.variables)
        and prompt.get("format") == spec.format
        and prompt.get("template_engine") == spec.template_engine
        and (prompt.get("published_at") is None or isinstance(prompt["published_at"], str))
        and isinstance(checksum, str)
        and checksum == _sha256(content)
    )


def _template_from_bundle(bundle: Any, name: str) -> str | None:
    resolved = _template_from_bundle_with_metadata(bundle, name)
    return resolved[0] if resolved is not None else None


def _template_from_bundle_with_metadata(
    bundle: Any,
    name: str,
) -> tuple[str, dict[str, str | None]] | None:
    if not isinstance(bundle, dict):
        return None
    prompts = bundle.get("prompts")
    if not isinstance(prompts, dict):
        return None
    prompt = prompts.get(name)
    if not isinstance(prompt, dict):
        return None
    content = prompt.get("content")
    checksum = prompt.get("content_sha256")
    if not isinstance(content, str) or not content or checksum != _sha256(content):
        return None
    slug = prompt.get("slug")
    version = prompt.get("version")
    revision = bundle.get("revision")
    if not all(isinstance(value, str) and value for value in (slug, version, revision)):
        return None
    return content, {
        "source": "prompthub",
        "prompt_slug": slug,
        "prompt_version": version,
        "prompt_revision": revision,
        "format": prompt["format"],
        "template_engine": prompt["template_engine"],
    }


def _load_active_bundle_payload(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    global _BUNDLE_CACHE
    now = time.monotonic()
    if use_cache and _BUNDLE_CACHE is not None and now - _BUNDLE_CACHE[0] < _BUNDLE_CACHE_TTL_SECONDS:
        return _BUNDLE_CACHE[1]

    session: Session | None = None
    try:
        session = session_factory()
        rows = (
            session.query(RuntimeConfigEntry)
            .filter(
                RuntimeConfigEntry.namespace == PROMPT_BUNDLE_NAMESPACE,
                RuntimeConfigEntry.key == PROMPT_BUNDLE_STORAGE_KEY,
                RuntimeConfigEntry.payload["project_slug"].as_string() == settings.PROMPTHUB_PROJECT_SLUG,
                RuntimeConfigEntry.is_active.is_(True),
            )
            .order_by(RuntimeConfigEntry.updated_at.desc(), RuntimeConfigEntry.created_at.desc())
            .limit(10)
            .all()
        )
        payload = next((row.payload for row in rows if validate_stored_bundle_payload(row.payload)), None)
    except Exception as exc:
        logger.warning(f"prompt_bundle: 读取 active LKG 失败: {exc}")
        payload = None
    finally:
        if session is not None:
            session.close()
    if use_cache:
        _BUNDLE_CACHE = (now, payload)
    return payload


def _is_pinned_during_p0_transition(name: str) -> bool:
    """P0 过渡未完成时，原本由代码提供有效值的 key 继续钉在代码默认值上。"""

    return not settings.PROMPT_P0_BASELINE_ATTESTED and name in PRE_P0_CODE_ONLY_KEYS


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
