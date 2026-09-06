"""最终运行时仅接收完整 Jinja2；历史旧契约只能保留，不能重新消费。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.prompt_bundle import validate_published_bundle, validate_stored_bundle_payload


def legacy_contract():
    import hashlib

    source = (Path(__file__).parent / "fixtures/prompt_bundle/legacy_v2_contract.json").read_bytes()
    assert hashlib.sha256(source).hexdigest() == "442674b68077a82e1d6d4b5a2d6c290e92bf8842eec3106d05f620376bfbd090"
    return json.loads(source)


def test_final_runtime_rejects_legacy_publication_storage_and_renderer():
    from app.core.prompt_template_engine import render_prompt_template

    material = legacy_contract()
    bundle = material["bundle"]
    bundle["prompts"] = tuple(SimpleNamespace(**item) for item in bundle["prompts"])
    assert not validate_stored_bundle_payload(material["payload"])
    with pytest.raises(ValueError):
        validate_published_bundle(SimpleNamespace(**bundle))
    with pytest.raises(ValueError):
        render_prompt_template("{content}", "none", {"content": "正文"})


def test_defaults_and_catalog_are_jinja_without_dual_engine_runtime():
    from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
    from app.ai.prompts.prompt_manager import prompt_manager
    from app.core.prompt_catalog import CATALOG_VERSION, DEFAULT_TEMPLATE_ENGINE, PROMPT_SPECS
    from app.core.prompt_snapshot import build_bundle_snapshot, use_prompt_snapshot

    assert CATALOG_VERSION == "2026-09-06.2" and DEFAULT_TEMPLATE_ENGINE == "jinja2"
    assert all(spec.template_engine == "jinja2" for spec in PROMPT_SPECS)
    frozen = build_bundle_snapshot(DEFAULT_PROMPT_TEMPLATES, payload=None, classifier_prompt="")
    with use_prompt_snapshot(frozen):
        assert (
            prompt_manager.format_prompt("file_analysis", query="问题", file_content="正文")
            == "请分析以下文件并回答问题。\n\n问题: 问题\n\n正文"
        )
    assert all(item.template_engine == "jinja2" for item in frozen.templates)
    source = (Path(__file__).resolve().parents[1] / "app/core/prompt_template_engine.py").read_text()
    assert "string.Formatter" not in source and ".format(**" not in source


def test_legacy_initial_migration_cli_is_closed_before_any_admin_client(monkeypatch):
    from scripts import migrate_prompts_to_prompthub as migration

    called = []
    monkeypatch.setattr(migration, "PromptHubAdminClient", lambda **kwargs: called.append(kwargs))
    monkeypatch.setattr("sys.argv", ["migrate_prompts_to_prompthub.py", "--apply"])
    with pytest.raises(SystemExit, match="独立桥接"):
        migration.main()
    assert called == []
