"""本地 Prompt 集合的冻结与解析。

Prompt 正文只来自随代码发布的本地资源；本模块不访问数据库或远端服务。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.prompt_catalog import DEFAULT_TEMPLATE_ENGINE, PROMPT_SPEC_BY_KEY
from app.core.prompt_snapshot import (
    PromptBundleSnapshot,
    build_bundle_snapshot,
    current_prompt_snapshot,
)
from app.core.prompt_snapshot import (
    use_prompt_snapshot as use_prompt_snapshot,
)


def freeze_prompt_bundle(defaults: Mapping[str, str], *, classifier_prompt: str = "") -> PromptBundleSnapshot:
    """冻结本次调用使用的完整本地 Prompt 集合。"""

    return build_bundle_snapshot(defaults, classifier_prompt=classifier_prompt)


def resolve_prompt_template(name: str, fallback: str) -> str:
    """从当前冻结集合解析 Prompt；无冻结上下文时使用本地默认正文。"""

    template, _metadata = resolve_prompt_template_with_metadata(name, fallback)
    return template


def resolve_prompt_template_with_metadata(name: str, fallback: str) -> tuple[str, dict[str, str | None]]:
    frozen = current_prompt_snapshot()
    if frozen is not None:
        return frozen.resolve(name)

    spec = PROMPT_SPEC_BY_KEY.get(name)
    return fallback, {
        "source": "code-default",
        "source_kind": "code_default",
        "effective_revision": None,
        "prompt_slug": spec.slug if spec is not None else name,
        "prompt_version": "code-default",
        "prompt_revision": None,
        "format": spec.format if spec is not None else "text",
        "template_engine": spec.template_engine if spec is not None else DEFAULT_TEMPLATE_ENGINE,
    }
