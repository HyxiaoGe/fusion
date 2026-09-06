"""两个完整引擎契约的桥接；渲染真值独立于 Fusion 实现。"""

import copy

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload
from app.core.prompt_catalog import PROMPT_SPECS
from test.test_prompt_bundle_v2_integrity import canonical_revision, published_v2_fixture


def jinja_bundle():
    bundle = published_v2_fixture()
    for spec, item in zip(PROMPT_SPECS, bundle.prompts, strict=True):
        item.template_engine = "jinja2"
        for variable in spec.variables:
            item.content = item.content.replace("{" + variable + "}", "{{ " + variable + " }}")
    bundle.revision = canonical_revision(bundle)
    return bundle


def test_final_jinja_profile_has_current_catalog():
    payload = validate_published_bundle(jinja_bundle())
    assert payload["catalog_version"] == "2026-09-06.2"
    assert validate_stored_bundle_payload(payload)


@pytest.mark.parametrize("change", ["mixed", "format", "unknown", "missing", "syntax", "constant"])
def test_invalid_engine_contract_rejects_whole_bundle(change):
    bundle = jinja_bundle()
    if change == "mixed":
        bundle.prompts[0].template_engine = "none"
    elif change == "format":
        bundle.prompts[-1].format = "markdown"
    elif change == "constant":
        bundle.prompts[0].content = "{{ 1 + 1 }}"
    else:
        bundle.prompts[-1].content = {
            "unknown": "{{ query }} {{ file_content }} {{ secret }}",
            "missing": "{{ query }}",
            "syntax": "{{ query }} {{ file_content ",
        }[change]
    bundle.revision = canonical_revision(bundle)
    with pytest.raises(ValueError):
        validate_published_bundle(bundle)


def test_local_checksum_cannot_launder_catalog_engine_mismatch():
    from app.core.prompt_bundle_integrity import compute_local_payload_checksum

    payload = validate_published_bundle(jinja_bundle())
    payload["catalog_version"] = "2026-09-06.1"
    payload["local_payload_checksum"] = compute_local_payload_checksum(payload)
    assert not validate_stored_bundle_payload(payload)


def test_all_templates_render_identically_to_independent_prompthub_contract():
    from app.ai.prompts.prompt_manager import prompt_manager
    from app.core.prompt_snapshot import build_bundle_snapshot, use_prompt_snapshot

    payload = validate_published_bundle(jinja_bundle())
    snapshot = build_bundle_snapshot(DEFAULT_PROMPT_TEMPLATES, payload=payload, classifier_prompt="")
    reference = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)
    data = {"content": "中文 <tag> {{ 原样 }}\r\n🙂", "query": "问题 {x}", "file_content": "文件\n尾行\n"}
    for spec in PROMPT_SPECS:
        body = payload["prompts"][spec.key]["content"]
        values = {key: data[key] for key in spec.variables}
        with use_prompt_snapshot(snapshot):
            actual = (
                prompt_manager.format_prompt(spec.key, **values) if spec.variables else snapshot.resolve(spec.key)[0]
            )
        assert actual.encode("utf-8") == reference.from_string(body).render(**values).encode("utf-8")


def test_snapshot_keeps_engine_after_active_switch_and_caller_mutation(monkeypatch):
    from app.ai.prompts.prompt_manager import prompt_manager
    from app.core import prompt_bundle
    from app.core.prompt_snapshot import build_bundle_snapshot, use_prompt_snapshot

    old = validate_published_bundle(published_v2_fixture())
    new = validate_published_bundle(jinja_bundle())
    before = build_bundle_snapshot(DEFAULT_PROMPT_TEMPLATES, payload=old, classifier_prompt="")
    after = build_bundle_snapshot(DEFAULT_PROMPT_TEMPLATES, payload=new, classifier_prompt="")
    identity = copy.deepcopy(before.identity())
    monkeypatch.setattr(prompt_bundle, "_load_active_bundle_payload", lambda: new)
    old["prompts"]["generate_title"]["template_engine"] = "jinja2"
    for frozen, engine in ((before, "jinja2"), (after, "jinja2"), (before, "jinja2")):
        with use_prompt_snapshot(frozen):
            assert frozen.resolve("generate_title")[1]["template_engine"] == engine
            assert prompt_manager.format_prompt("generate_title", content="{{ 中文 }}").endswith("{{ 中文 }}")
    assert before.identity() == identity
    assert after.catalog_version == "2026-09-06.2"


def test_strict_undefined_sandbox_and_trailing_newline():
    from app.core.prompt_template_engine import render_prompt_template

    assert render_prompt_template("{{ content }}\n", "jinja2", {"content": "<原样>"}) == "<原样>\n"
    with pytest.raises(ValueError):
        render_prompt_template("{{ content }}", "jinja2", {})
    with pytest.raises(ValueError):
        render_prompt_template("{{ content.__class__.__mro__ }}", "jinja2", {"content": "x"})


def test_final_p0_gate_requires_jinja_raw_baseline(monkeypatch):
    from app.core.config import settings
    from app.services.prompt_effective_map import assert_p0_transition_gate

    monkeypatch.setattr(settings, "PROMPT_P0_BASELINE_ATTESTED", True)
    new = validate_published_bundle(jinja_bundle())
    contents = {key: item["content"] for key, item in new["prompts"].items()}
    assert_p0_transition_gate(DEFAULT_PROMPT_TEMPLATES, template_engine="jinja2")
    assert_p0_transition_gate(contents, template_engine="jinja2")
    with pytest.raises(RuntimeError):
        assert_p0_transition_gate(contents, template_engine="none")
    contents["file_content_enhancement"] += "\n"
    with pytest.raises(RuntimeError):
        assert_p0_transition_gate(contents, template_engine="jinja2")


def test_jinja_activation_requires_attestation_even_if_legacy_gate_is_transitional(monkeypatch):
    from app.core.config import settings
    from app.services.prompt_effective_map import assert_payload_p0_gate

    monkeypatch.setattr(settings, "PROMPT_P0_BASELINE_ATTESTED", False)
    payload = validate_published_bundle(jinja_bundle())
    with pytest.raises(RuntimeError, match="attestation"):
        assert_payload_p0_gate(payload)
