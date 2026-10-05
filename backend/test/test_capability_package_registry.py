"""能力包注册表与各处副本的一致性：新增或修改能力包时漏改任何一处都会在这里失败。"""

from __future__ import annotations

import re
from typing import get_args

from app.ai.prompts.runtime_prompt_store import get_runtime_prompt_source
from app.schemas.trajectory import CapabilityPackageId, CapabilityReasonCode
from app.utils.run_capability_contract import (
    CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER,
    CAPABILITY_MODEL_PACKAGE_IDS,
    CAPABILITY_PACKAGES,
    CAPABILITY_REASON_CODES,
)

_PLAN_MODES = frozenset({"auto", "on", "off"})


def test_schema_literals_match_registry():
    assert set(get_args(CapabilityPackageId)) == set(CAPABILITY_PACKAGES)
    assert set(get_args(CapabilityReasonCode)) == CAPABILITY_REASON_CODES


def test_each_spec_is_internally_consistent():
    for package_id, spec in CAPABILITY_PACKAGES.items():
        canonical = tuple(name for name in CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER if name in spec.tools)
        assert spec.tools == canonical, package_id
        assert spec.reason_code_options and spec.confidence_options, package_id
        assert set(spec.confidence_options) <= {"high", "medium", "low"}, package_id
        assert spec.resolution_mode in {"routed", "degraded", "clarification"}, package_id
        assert spec.plan_modes and spec.plan_modes <= _PLAN_MODES, package_id
        assert not (spec.mcp_aliases and spec.tools), package_id
        if spec.include_current_date is not None:
            assert spec.model_include_current_date is None, package_id
        if spec.requires_primary_tool:
            assert len(spec.tools) > 1, package_id


def test_reason_codes_are_not_shared_between_packages():
    owners: dict[str, str] = {}
    for package_id, spec in CAPABILITY_PACKAGES.items():
        for option in spec.reason_code_options:
            for code in option:
                assert owners.setdefault(code, package_id) == package_id, code


def test_classifier_prompt_lists_every_model_package_with_its_tools():
    prompt = get_runtime_prompt_source("classifier.system")
    listed = dict(re.findall(r"^- ([a-z_]+): (.*)$", prompt, flags=re.MULTILINE))
    for package_id in CAPABILITY_MODEL_PACKAGE_IDS:
        assert package_id in listed, package_id
        # 这两个包由模型自选工具子集，Prompt 里没有固定工具列表。
        if package_id in {"mcp_explicit", "mixed_itinerary"}:
            continue
        spec = CAPABILITY_PACKAGES[package_id]
        assert listed[package_id].endswith(f"[{','.join(spec.tools)}]."), package_id
    for package_id in set(CAPABILITY_PACKAGES) - CAPABILITY_MODEL_PACKAGE_IDS:
        assert package_id not in listed, package_id
