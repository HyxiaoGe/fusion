"""P0 切换门禁：抓取部署前 effective map、生成基线 bundle、逐 key 字节校验。

P0 让此前直接返回代码常量的 5 个 getter 首次开始消费 active bundle，同时移除
catalog key 对 legacy ``prompt_template`` 命名空间的回退。若直接切换，管理员此前
在 PromptHub 或 legacy 配置里留下的正文会在上线瞬间生效 / 失效，因此必须先把
**部署前的实际有效值**固化成基线 bundle，并逐 key 字节复核后才允许 apply。

基线**不得**由「全部代码默认值」生成：11 项里只有 5 项的当前有效值确实是代码常量，
另外 6 项已经在消费 bundle 或 legacy 配置，用全代码默认值发布会在部署前就先改变
这 6 条线的行为，把风险从 5 项扩大到 11 项。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.prompt_bundle import load_stored_active_bundle_payload
from app.core.prompt_catalog import (
    CATALOG_VERSION,
    PRE_P0_CODE_ONLY_KEYS,
    PROMPT_SPECS,
    PromptSpec,
)
from app.core.runtime_config import SessionFactory, get_runtime_config_payload
from app.db.database import SessionLocal
from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES


class EffectiveBaselineMismatch(RuntimeError):
    """待激活 bundle 与部署前 effective map 不一致，必须 fail closed。"""


@dataclass(frozen=True)
class EffectiveEntry:
    """某个 catalog key 在部署前的真实有效值及其来源。"""

    key: str
    slug: str
    content: str
    variables: tuple[str, ...]
    source: str
    source_version: str

    @property
    def content_sha256(self) -> str:
        return _sha256(self.content)


def capture_pre_p0_effective_map(
    *,
    session_factory: SessionFactory = SessionLocal,
    sync_mode: str | None = None,
) -> dict[str, EffectiveEntry]:
    """复现 P0 之前的解析语义，抓取 11 项的真实有效值。

    - `PRE_P0_CODE_ONLY_KEYS` 取代码常量；
    - 其余 key 按 P0 之前的 active bundle -> legacy Runtime Config -> 代码默认值解析。

    `sync_mode` 必须是**被抓取环境**当时的消费模式，而不是本进程的设置：抓取通常在
    切模式/换镜像之前从一次性容器里跑，两者可能不同。因此 stored LKG 一律用
    `load_stored_active_bundle_payload()` 直读（不受模式开关影响），再按传入的
    `sync_mode` 决定 P0 之前是否真的会命中它。
    """

    effective_sync_mode = (sync_mode or settings.PROMPTHUB_SYNC_MODE or "").strip().lower()
    bundle = load_stored_active_bundle_payload() if effective_sync_mode == "apply" else None
    effective_map: dict[str, EffectiveEntry] = {}
    for spec in PROMPT_SPECS:
        effective_map[spec.key] = _capture_entry(spec, bundle=bundle, session_factory=session_factory)
    return effective_map


def build_effective_baseline_bundle(effective_map: dict[str, EffectiveEntry]) -> dict[str, Any]:
    """把 effective map 转成可发布到 PromptHub 的完整基线 bundle 载荷。"""

    _assert_covers_catalog(effective_map)
    return {
        "project_slug": settings.PROMPTHUB_PROJECT_SLUG,
        "catalog_version": CATALOG_VERSION,
        "prompts": [
            {
                "slug": entry.slug,
                "content": entry.content,
                "variables": list(entry.variables),
                "content_sha256": entry.content_sha256,
                "captured_source": entry.source,
                "captured_version": entry.source_version,
            }
            for entry in sorted(effective_map.values(), key=lambda item: item.slug)
        ],
    }


def verify_bundle_matches_effective_map(
    bundle_prompts: dict[str, str],
    effective_map: dict[str, EffectiveEntry],
) -> list[str]:
    """逐 key 比对 UTF-8 原始字节，返回不一致说明；空列表表示可以进入 apply。

    比对不做 strip、不做换行归一化：任何字节差异都会改变模型可见正文。
    """

    _assert_covers_catalog(effective_map)
    mismatches: list[str] = []
    expected_keys = set(effective_map)
    actual_keys = set(bundle_prompts)
    for key in sorted(expected_keys - actual_keys):
        mismatches.append(f"{key}: 待激活 bundle 缺少该条目")
    for key in sorted(actual_keys - expected_keys):
        mismatches.append(f"{key}: 待激活 bundle 含 catalog 之外的条目")
    for key in sorted(expected_keys & actual_keys):
        expected = effective_map[key].content.encode("utf-8")
        actual = bundle_prompts[key].encode("utf-8")
        if expected != actual:
            mismatches.append(
                f"{key}: 与部署前 effective map 字节不一致"
                f"（expected_sha256={_sha256_bytes(expected)} actual_sha256={_sha256_bytes(actual)}）"
            )
    return mismatches


def assert_bundle_matches_effective_map(
    bundle_prompts: dict[str, str],
    effective_map: dict[str, EffectiveEntry],
) -> None:
    """字节校验失败即 fail closed，调用方不得吞掉该异常继续 apply。"""

    mismatches = verify_bundle_matches_effective_map(bundle_prompts, effective_map)
    if mismatches:
        raise EffectiveBaselineMismatch("; ".join(mismatches))


def assert_p0_transition_gate(bundle_prompts: dict[str, str]) -> None:
    """校验 attestation 这一**声明**是否属实，不可绕过。

    过渡期（未 attested）`PRE_P0_CODE_ONLY_KEYS` 被钉在代码默认值上，bundle 里这几项
    的内容不参与消费，因此无需拦截——拦截反而会挡住过渡期发布新基线 bundle。

    `PROMPT_P0_BASELINE_ATTESTED=true` 是运维给出的「已完成 capture 与逐 key 复核」
    声明，置位后这些 key 立即改由 bundle 提供。本门禁在激活与 apply 启动两处校验该
    声明：若 bundle 里这些项与代码默认值不是逐字节相等，说明声明与事实不符，fail closed。

    该断言随 P5 英文化一并移除——届时这些正文会被有意改写，不再等于代码默认值。
    """

    if not settings.PROMPT_P0_BASELINE_ATTESTED:
        return
    mismatches = []
    for key in sorted(PRE_P0_CODE_ONLY_KEYS):
        expected = DEFAULT_PROMPT_TEMPLATES[key].encode("utf-8")
        actual = bundle_prompts.get(key, "").encode("utf-8")
        if expected != actual:
            mismatches.append(
                f"{key}: 与代码默认值字节不一致"
                f"（expected_sha256={_sha256_bytes(expected)} actual_sha256={_sha256_bytes(actual)}）"
            )
    if mismatches:
        raise EffectiveBaselineMismatch(
            "PROMPT_P0_BASELINE_ATTESTED 已置位，但 bundle 与代码默认值不一致，"
            "说明基线复核未真正通过：" + "; ".join(mismatches)
        )


def bundle_payload_contents(payload: dict[str, Any] | None) -> dict[str, str]:
    """从已校验的 stored bundle payload 提取 {key: 正文}。"""

    prompts = (payload or {}).get("prompts") or {}
    return {key: item.get("content", "") for key, item in prompts.items() if isinstance(item, dict)}


def code_default_effective_revision() -> str:
    """代码默认值路径的确定性摘要，供降级路径记录可审计身份。

    这**不是** PromptHub 的 `_bundle_revision` 算法：`PromptSpec` 没有 version 字段，
    而 PromptHub canonical 要求每项都有 version，两者输入不同、摘要永不相等，
    必须由 `source_kind` 区分。排序键与序列化参数保持一致，便于复用同一工具。
    """

    canonical = json.dumps(
        {
            "catalog_version": CATALOG_VERSION,
            "prompts": [
                {
                    "key": spec.key,
                    "slug": spec.slug,
                    "content_sha256": _sha256(DEFAULT_PROMPT_TEMPLATES[spec.key]),
                    "variables": list(spec.variables),
                }
                for spec in sorted(PROMPT_SPECS, key=lambda item: item.slug)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(canonical)


def _capture_entry(
    spec: PromptSpec,
    *,
    bundle: dict[str, Any] | None,
    session_factory: SessionFactory,
) -> EffectiveEntry:
    code_default = DEFAULT_PROMPT_TEMPLATES[spec.key]
    if spec.key in PRE_P0_CODE_ONLY_KEYS:
        return _entry(spec, code_default, "code-default", "code-default")

    prompt = (bundle or {}).get("prompts", {}).get(spec.key)
    if isinstance(prompt, dict) and isinstance(prompt.get("content"), str) and prompt["content"]:
        return _entry(spec, prompt["content"], "prompthub", str(prompt.get("version") or "unknown"))

    payload, meta = get_runtime_config_payload(
        "prompt_template",
        spec.key,
        {"template": code_default},
        session_factory=session_factory,
        use_cache=False,
    )
    template = payload.get("template")
    if isinstance(template, str) and template:
        return _entry(spec, template, str(meta.get("source", "code-default")), str(meta.get("version", "code-default")))
    return _entry(spec, code_default, "code-default", "code-default")


def _entry(spec: PromptSpec, content: str, source: str, source_version: str) -> EffectiveEntry:
    return EffectiveEntry(
        key=spec.key,
        slug=spec.slug,
        content=content,
        variables=spec.variables,
        source=source,
        source_version=source_version,
    )


def _assert_covers_catalog(effective_map: dict[str, EffectiveEntry]) -> None:
    expected = {spec.key for spec in PROMPT_SPECS}
    if set(effective_map) != expected:
        missing = sorted(expected - set(effective_map))
        extra = sorted(set(effective_map) - expected)
        raise EffectiveBaselineMismatch(f"effective map 与 catalog 不一致: missing={missing} extra={extra}")


def _sha256(content: str) -> str:
    return _sha256_bytes(content.encode("utf-8"))


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def resolve_effective_map_with_current_code() -> dict[str, EffectiveEntry]:
    """返回**候选代码在当前进程配置下**实际解析出的 11 项正文。

    迁移安全判据只能是「部署前 effective map 与候选实际输出逐 key 字节相等」。
    `captured_source` 不能承担这个判据：过渡期 `PRE_P0_CODE_ONLY_KEYS` 按构造就是
    `code-default`，而其余 key 是否安全取决于正文本身而非来源标签。
    """

    from app.core.prompt_bundle import resolve_prompt_template_with_metadata

    resolved: dict[str, EffectiveEntry] = {}
    for spec in PROMPT_SPECS:
        content, metadata = resolve_prompt_template_with_metadata(spec.key, DEFAULT_PROMPT_TEMPLATES[spec.key])
        resolved[spec.key] = _entry(
            spec,
            content,
            str(metadata.get("source") or "unknown"),
            str(metadata.get("prompt_version") or "unknown"),
        )
    return resolved


def diff_effective_maps(
    baseline: dict[str, EffectiveEntry],
    candidate: dict[str, EffectiveEntry],
) -> list[str]:
    """逐 key UTF-8 原始字节比对两份 effective map，返回不一致说明。"""

    _assert_covers_catalog(baseline)
    _assert_covers_catalog(candidate)
    mismatches: list[str] = []
    for key in sorted(baseline):
        expected = baseline[key].content.encode("utf-8")
        actual = candidate[key].content.encode("utf-8")
        if expected != actual:
            mismatches.append(
                f"{key}: 部署前有效值与候选解析结果字节不一致"
                f"（baseline_source={baseline[key].source} candidate_source={candidate[key].source}"
                f" expected_sha256={_sha256_bytes(expected)} actual_sha256={_sha256_bytes(actual)}）"
            )
    return mismatches
