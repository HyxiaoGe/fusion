"""PromptHub 来源身份与本地封装摘要，二者采用不同的材料边界。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

PROMPT_BUNDLE_NAMESPACE = "prompt_bundle"
PROMPT_BUNDLE_STORAGE_KEY = "fusion:v2"
PROMPT_BUNDLE_LOGICAL_CATALOG = "fusion"
PROMPT_BUNDLE_SCHEMA_VERSION = 2


class PromptBundleValidationError(ValueError):
    """完整包不符合当前 catalog 契约。"""


class PromptBundleRevisionConflict(PromptBundleValidationError):
    """来源身份与实际材料冲突，必须独立诊断并保持本地行。"""


def canonical_json_checksum(material: Any) -> str:
    """遵守 PromptHub JSON 编码参数，拒绝非 JSON 数值。"""
    raw = json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_source_revision(project_slug: str, prompts: Iterable[Mapping[str, Any]]) -> str:
    """原始 variables 的结构及列表顺序参与身份，不混入 engine 或本地 schema。"""
    return canonical_json_checksum(
        {
            "project_slug": project_slug,
            "prompts": [
                {
                    "slug": item["slug"],
                    "version": item["version"],
                    "content_sha256": hashlib.sha256(item["content"].encode("utf-8")).hexdigest(),
                    "variables": item["raw_variables"],
                }
                for item in sorted(prompts, key=lambda item: item["slug"])
            ],
        }
    )


def compute_local_payload_checksum(payload: Mapping[str, Any]) -> str:
    """校验整个持久封装，仅排除 checksum 字段本身。

    包含 schema/catalog/project/revision、每项原始正文及其摘要、版本、归一化变量、
    原始 variables、format/engine、发布时间；不代替来源 canonical 或业务契约校验。
    """
    return canonical_json_checksum({key: value for key, value in payload.items() if key != "local_payload_checksum"})


def normalize_variable_names(raw_variables: Any) -> tuple[str, ...]:
    """仅供业务变量校验；原始列表始终另行保留。"""
    if not isinstance(raw_variables, list):
        raise PromptBundleValidationError("variables 必须是原始 JSON 列表")
    names = []
    for variable in raw_variables:
        name = variable.get("name") if isinstance(variable, dict) else variable
        if not isinstance(name, str) or not name:
            raise PromptBundleValidationError("variable name 无效")
        names.append(name)
    canonical_json_checksum(raw_variables)
    return tuple(names)
