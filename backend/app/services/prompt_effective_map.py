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
from app.core.prompt_bundle import get_active_prompt_bundle_payload
from app.core.prompt_catalog import CATALOG_VERSION, PROMPT_SPECS, PromptSpec
from app.core.runtime_config import SessionFactory, get_runtime_config_payload
from app.db.database import SessionLocal
from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES

# P0 之前这些 key 的 getter 直接 return 代码常量，从不解析 bundle 或 legacy 配置。
# 抓取部署前 effective map 时必须复现该语义，否则会把「本应是代码常量」的条目
# 误记成 bundle / legacy 值。
PRE_P0_CODE_ONLY_KEYS = frozenset(
    {
        "app_identity",
        "tool_usage_contract",
        "no_tool_network_boundary",
        "no_vision_file_boundary",
        "continuation_system",
    }
)


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
) -> dict[str, EffectiveEntry]:
    """复现 P0 之前的解析语义，抓取 11 项的真实有效值。

    - `PRE_P0_CODE_ONLY_KEYS` 取代码常量；
    - 其余 key 按 P0 之前的 active bundle -> legacy Runtime Config -> 代码默认值解析。
    """

    # get_active_prompt_bundle_payload() 已含 apply 模式判定与 stored payload 校验。
    bundle = get_active_prompt_bundle_payload()
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
    """P0 切换门禁：不可绕过，在任何 bundle 激活前与 apply 模式启动时强制执行。

    P0 让 `PRE_P0_CODE_ONLY_KEYS` 五项首次开始消费 bundle。若此时 bundle 里这五项的
    正文与代码常量不同，模型可见正文会在切换瞬间改变——这正是 P0 必须排除的情形。
    因此在 P0 过渡完成前，激活任何 bundle 都要求这五项与代码默认值**逐字节相等**。

    `PROMPT_P0_BASELINE_ATTESTED=true` 表示过渡已完成并经复审，此后这五项与其余六项
    一样可以正常热更新。该开关只应在 effective baseline 校验通过后由发布流程置位。
    """

    if settings.PROMPT_P0_BASELINE_ATTESTED:
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
            "P0 过渡门禁未通过，禁止激活该 bundle；请先跑 effective baseline 校验并置位 "
            "PROMPT_P0_BASELINE_ATTESTED: " + "; ".join(mismatches)
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
