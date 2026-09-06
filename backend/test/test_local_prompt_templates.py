"""本地外置 Prompt 的完整性、英文化与启动期加载契约。"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone

import pytest

from app.ai.prompts.agent_loop import VISIBLE_RESPONSE_LANGUAGE_PROMPT, build_current_date_system_prompt
from app.ai.prompts.local_templates import PROMPT_TEMPLATE_DIR, load_prompt_templates
from app.core.prompt_bundle import freeze_prompt_bundle
from app.core.prompt_catalog import PROMPT_SPECS

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def test_local_prompt_files_exactly_cover_catalog_and_use_english_bodies():
    templates = load_prompt_templates()

    assert set(templates) == {spec.key for spec in PROMPT_SPECS}
    assert not any(_CJK.search(body) for body in templates.values())


def test_loader_rejects_missing_extra_and_invalid_template(tmp_path):
    template_dir = tmp_path / "templates"
    shutil.copytree(PROMPT_TEMPLATE_DIR, template_dir)

    (template_dir / "app-identity.j2").unlink()
    with pytest.raises(ValueError, match="缺少"):
        load_prompt_templates(template_dir)

    shutil.copytree(PROMPT_TEMPLATE_DIR, template_dir, dirs_exist_ok=True)
    (template_dir / "unexpected.j2").write_text("Unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="多余"):
        load_prompt_templates(template_dir)

    (template_dir / "unexpected.j2").unlink()
    (template_dir / "generate-title.j2").write_text("{{ missing }}", encoding="utf-8")
    with pytest.raises(ValueError, match="变量契约"):
        load_prompt_templates(template_dir)


def test_runtime_defaults_are_loaded_once_and_snapshots_stay_immutable(tmp_path):
    template_dir = tmp_path / "templates"
    shutil.copytree(PROMPT_TEMPLATE_DIR, template_dir)

    loaded_at_startup = load_prompt_templates(template_dir)
    first = freeze_prompt_bundle(loaded_at_startup)
    identity_path = template_dir / "app-identity.j2"
    identity_path.write_text(
        identity_path.read_text(encoding="utf-8") + "\nUpdated locally.\n",
        encoding="utf-8",
    )
    second = freeze_prompt_bundle(loaded_at_startup)

    assert not first.resolve("app_identity")[0].endswith("Updated locally.\n")
    assert not second.resolve("app_identity")[0].endswith("Updated locally.\n")
    assert first.effective_revision == second.effective_revision


def test_dynamic_global_system_sections_are_also_written_in_english():
    current_date = build_current_date_system_prompt(datetime(2026, 9, 6, 12, 30, tzinfo=timezone.utc))

    assert not _CJK.search(current_date)
    assert "Current date" in current_date
    assert not _CJK.search(VISIBLE_RESPONSE_LANGUAGE_PROMPT)
    assert "last actual user request" in VISIBLE_RESPONSE_LANGUAGE_PROMPT
