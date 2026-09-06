"""基线提案来自已校验的完整原始发布包，不能覆盖线上定制正文。"""

import importlib.util
from pathlib import Path

import pytest

from test.test_prompt_bundle_v2_integrity import canonical_revision, published_v2_fixture


def tool():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_jinja_prompt_baseline.py"
    spec = importlib.util.spec_from_file_location("jinja_baseline_proposal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_proposal_preserves_original_custom_content_and_raw_variables():
    bundle = published_v2_fixture()
    bundle.prompts[5].content += "\n线上特定总结约束"
    bundle.prompts[7].content = '定制标题 {{"JSON": true}}\n{content}\n'
    bundle.revision = canonical_revision(bundle)
    result = tool().prepare(bundle)
    assert result["source_revision"] == bundle.revision
    assert "revision" not in result
    items = {item["slug"]: item for item in result["prompts"]}
    assert items["limit-summary"]["content"] == bundle.prompts[5].content
    assert items["generate-title"]["content"] == '定制标题 {"JSON": true}\n{{ content }}\n'
    assert items["file-analysis"]["variables"] == bundle.prompts[-2].raw_variables
    assert all(item["template_engine"] == "jinja2" for item in result["prompts"])


@pytest.mark.parametrize("body", ["{content!r}", "{content:>20}", "模板\r\n{content}"])
def test_nontrivial_conversion_or_newline_change_requires_manual_review(body):
    bundle = published_v2_fixture()
    bundle.prompts[7].content = body
    bundle.revision = canonical_revision(bundle)
    with pytest.raises(ValueError):
        tool().prepare(bundle)


def test_proposal_has_no_remote_or_database_write_path():
    source = Path(tool().__file__).read_text()
    assert "httpx" not in source and "SessionLocal" not in source
    assert "O_EXCL" in source
