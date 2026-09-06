"""使用独立 canonical 夹具验证 v2，避免把生产摘要函数当作测试真值。"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from test.test_prompt_bundle import _published_bundle


def canonical_revision(bundle):
    material = {
        "project_slug": bundle.project_slug,
        "prompts": [
            {
                "slug": item.slug,
                "version": item.version,
                "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                "variables": item.raw_variables,
            }
            for item in sorted(bundle.prompts, key=lambda item: item.slug)
        ],
    }
    raw = json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def published_v2_fixture():
    original = _published_bundle()
    prompts = tuple(
        SimpleNamespace(
            **{
                **vars(item),
                "raw_variables": [
                    {"name": name, "required": True, "description": "中文定义"} for name in item.variables
                ],
            },
        )
        for item in original.prompts
    )
    bundle = SimpleNamespace(**{**vars(original), "prompts": prompts})
    bundle.revision = canonical_revision(bundle)
    return bundle


def test_v2_preserves_raw_variables_and_has_independent_local_checksum():
    from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload
    from app.core.prompt_catalog import CATALOG_VERSION

    bundle = published_v2_fixture()
    payload = validate_published_bundle(bundle)
    assert payload["schema_version"] == 2
    assert payload["catalog_version"] == CATALOG_VERSION
    assert payload["revision"] == canonical_revision(bundle)
    assert payload["prompts"]["file_analysis"]["raw_variables"] == bundle.prompts[-2].raw_variables
    assert payload["prompts"]["file_analysis"]["format"] == "text"
    assert payload["prompts"]["file_analysis"]["template_engine"] == "jinja2"
    checksum = payload["local_payload_checksum"]
    material = {key: value for key, value in payload.items() if key != "local_payload_checksum"}
    encoded = json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert checksum == hashlib.sha256(encoded).hexdigest()
    assert checksum != payload["revision"]
    assert validate_stored_bundle_payload(payload)
    bundle.prompts[-2].raw_variables[0]["description"] = "输入对象后续被改写"
    assert payload["prompts"]["file_analysis"]["raw_variables"][0]["description"] == "中文定义"


def test_raw_variable_order_cannot_be_replaced_by_normalized_names():
    from app.core.prompt_bundle import PromptBundleRevisionConflict, validate_published_bundle

    bundle = published_v2_fixture()
    bundle.prompts[-2].raw_variables.reverse()
    bundle.prompts[-2].variables = tuple(item["name"] for item in bundle.prompts[-2].raw_variables)
    with pytest.raises(PromptBundleRevisionConflict):
        validate_published_bundle(bundle)
    bundle.revision = canonical_revision(bundle)
    assert validate_published_bundle(bundle)["revision"] == bundle.revision


def test_well_formed_but_forged_revision_is_a_distinct_conflict():
    from app.core.prompt_bundle import PromptBundleRevisionConflict, validate_published_bundle

    bundle = published_v2_fixture()
    bundle.revision = "a" * 64
    with pytest.raises(PromptBundleRevisionConflict):
        validate_published_bundle(bundle)


@pytest.mark.parametrize("field", ["published_at", "template_engine", "format", "raw_variables"])
def test_local_metadata_corruption_is_rejected(field):
    from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload

    payload = validate_published_bundle(published_v2_fixture())
    payload["prompts"]["file_analysis"][field] = "损坏"
    assert not validate_stored_bundle_payload(payload)


def test_catalog_mismatch_and_old_schema_are_not_valid_v2():
    from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload

    payload = validate_published_bundle(published_v2_fixture())
    for change in ({"catalog_version": "unknown"}, {"schema_version": 1}):
        assert not validate_stored_bundle_payload({**payload, **change})


def test_stored_diagnostics_distinguish_catalog_drift_from_corruption():
    from app.core.prompt_bundle import diagnose_stored_bundle_payload, validate_published_bundle

    payload = validate_published_bundle(published_v2_fixture())
    assert diagnose_stored_bundle_payload(payload) == "valid"
    assert diagnose_stored_bundle_payload({**payload, "catalog_version": "unknown"}) == "catalog_mismatch"
    assert diagnose_stored_bundle_payload({**payload, "schema_version": 1}) == "schema_unsupported"
    assert diagnose_stored_bundle_payload({**payload, "local_payload_checksum": "a" * 64}) == "local_checksum_mismatch"


def test_catalog_format_contract_is_used_by_publication_and_stored_validation(monkeypatch):
    from dataclasses import replace

    from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload
    from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY, PROMPT_SPEC_BY_SLUG

    spec = replace(PROMPT_SPEC_BY_KEY["file_analysis"], format="markdown")
    monkeypatch.setitem(PROMPT_SPEC_BY_KEY, spec.key, spec)
    monkeypatch.setitem(PROMPT_SPEC_BY_SLUG, spec.slug, spec)
    bundle = published_v2_fixture()
    bundle.prompts[-2].format = "markdown"
    assert validate_stored_bundle_payload(validate_published_bundle(bundle))


def test_chinese_marker_is_not_a_publication_gate():
    from app.core.prompt_bundle import validate_published_bundle

    bundle = published_v2_fixture()
    bundle.prompts[0].content = "可用正文不依赖固定中文子串。"
    bundle.revision = canonical_revision(bundle)
    assert validate_published_bundle(bundle)["prompts"]["app_identity"]["content"] == bundle.prompts[0].content


def test_local_checksum_cannot_hide_source_content_tampering():
    from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload

    payload = copy.deepcopy(validate_published_bundle(published_v2_fixture()))
    item = payload["prompts"]["limit_summary"]
    item["content"] += "\n"
    item["content_sha256"] = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
    material = {key: value for key, value in payload.items() if key != "local_payload_checksum"}
    payload["local_payload_checksum"] = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert not validate_stored_bundle_payload(payload)
