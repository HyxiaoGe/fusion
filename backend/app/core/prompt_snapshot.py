"""不可变 Prompt 模板与任务局部读取源，不承担数据库或发布操作。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from app.core.prompt_catalog import CATALOG_VERSION, DEFAULT_TEMPLATE_ENGINE, PROMPT_SPECS


@dataclass(frozen=True)
class PromptTemplateSnapshot:
    key: str
    slug: str
    content: str
    variables: tuple[str, ...]
    version: str
    format: str = "text"
    template_engine: str = DEFAULT_TEMPLATE_ENGINE


@dataclass(frozen=True)
class PromptBundleSnapshot:
    source_kind: str
    source_revision: str | None
    effective_revision: str
    catalog_version: str
    templates: tuple[PromptTemplateSnapshot, ...]
    classifier_prompt: str

    def identity(self) -> dict:
        """仅返回审计身份，不将模板正文或用户内容带入轻量配置。"""
        return {
            "source_kind": self.source_kind,
            "source_revision": self.source_revision,
            "effective_revision": self.effective_revision,
            "catalog_version": self.catalog_version,
            "prompt_versions": {item.slug: item.version for item in self.templates},
        }

    def resolve(self, name: str) -> tuple[str, dict[str, str | None]]:
        item = next((item for item in self.templates if item.key == name), None)
        if item is None:
            raise ValueError(f"冻结 Prompt 中不存在 section: {name}")
        return item.content, {
            "source": "code-default",
            "source_kind": self.source_kind,
            "effective_revision": self.effective_revision,
            "prompt_slug": item.slug,
            "prompt_version": item.version,
            "prompt_revision": self.source_revision,
            "format": item.format,
            "template_engine": item.template_engine,
        }


class PromptSnapshotSource(Protocol):
    """Bundle 与分类后的 Run 快照共用同一只读解析契约。"""

    @property
    def classifier_prompt(self) -> str: ...

    def resolve(self, name: str) -> tuple[str, dict[str, str | None]]: ...


_CURRENT_SNAPSHOT: ContextVar[PromptSnapshotSource | None] = ContextVar("fusion_prompt_snapshot", default=None)


def current_prompt_snapshot() -> PromptSnapshotSource | None:
    return _CURRENT_SNAPSHOT.get()


@contextmanager
def use_prompt_snapshot(snapshot: PromptSnapshotSource) -> Iterator[None]:
    """作用域退出或取消时恢复来源；异步任务与 to_thread 保持各自身份。"""
    token = _CURRENT_SNAPSHOT.set(snapshot)
    try:
        yield
    finally:
        _CURRENT_SNAPSHOT.reset(token)


def build_bundle_snapshot(defaults: Mapping[str, str], *, classifier_prompt: str) -> PromptBundleSnapshot:
    if set(defaults) != {spec.key for spec in PROMPT_SPECS}:
        raise ValueError("代码默认 Prompt 必须完整覆盖 catalog")
    if any(not isinstance(body, str) or not body.strip() for body in defaults.values()):
        raise ValueError("代码默认 Prompt 正文不能为空")
    templates = tuple(
        PromptTemplateSnapshot(
            key=spec.key,
            slug=spec.slug,
            content=defaults[spec.key],
            variables=tuple(spec.variables),
            version="code-default",
            format=spec.format,
            template_engine=DEFAULT_TEMPLATE_ENGINE,
        )
        for spec in sorted(PROMPT_SPECS, key=lambda item: item.slug)
    )
    return PromptBundleSnapshot(
        source_kind="code_default",
        source_revision=None,
        effective_revision=_code_default_revision(templates),
        catalog_version=CATALOG_VERSION,
        templates=templates,
        classifier_prompt=classifier_prompt,
    )


def _code_default_revision(templates: tuple[PromptTemplateSnapshot, ...]) -> str:
    canonical = json.dumps(
        {
            "catalog_version": CATALOG_VERSION,
            "prompts": [
                {
                    "key": item.key,
                    "slug": item.slug,
                    "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                    "variables": list(item.variables),
                }
                for item in templates
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
