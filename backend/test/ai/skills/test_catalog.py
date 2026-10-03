from pathlib import Path

import pytest

from app.ai.skills import SkillFileError, discover_skills, read_skill, read_skill_reference
from app.ai.skills.catalog import parse_skill_document


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "Do the work step by step.\n") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return directory


def test_discovers_valid_skills_and_skips_invalid_ones(tmp_path):
    _write_skill(
        tmp_path,
        "trip-planning",
        "name: trip-planning\ndescription: >\n  Plan a trip\n  day by day.\nmetadata:\n"
        "  fusion-tools: weather_forecast route_compare\n  source: adapted",
    )
    _write_skill(tmp_path, "comparison-advice", "name: comparison-advice\ndescription: Compare options.")
    _write_skill(tmp_path, "wrong-dir", "name: other-name\ndescription: Name must match directory.")
    _write_skill(tmp_path, "Bad--Name", "name: Bad--Name\ndescription: Invalid name.")
    _write_skill(tmp_path, "no-description", "name: no-description")
    _write_skill(
        tmp_path,
        "unknown-tool",
        "name: unknown-tool\ndescription: Uses a tool Fusion does not have.\nmetadata:\n  fusion-tools: shell_exec",
    )
    _write_skill(tmp_path, "empty-body", "name: empty-body\ndescription: No instructions.", body="")
    (tmp_path / "not-a-skill").mkdir()

    entries = {entry.name: entry for entry in discover_skills(tmp_path)}

    assert set(entries) == {"comparison-advice", "trip-planning"}
    trip = entries["trip-planning"]
    assert trip.description == "Plan a trip day by day."
    assert trip.tool_names == frozenset({"weather_forecast", "route_compare"})
    assert entries["comparison-advice"].tool_names == frozenset()


def test_skill_is_listed_when_any_declared_tool_is_authorized_or_none_declared(tmp_path):
    _write_skill(
        tmp_path,
        "web-research",
        "name: web-research\ndescription: Research the web.\nmetadata:\n  fusion-tools: web_search url_read",
    )
    _write_skill(tmp_path, "comparison-advice", "name: comparison-advice\ndescription: Compare options.")
    entries = {entry.name: entry for entry in discover_skills(tmp_path)}

    assert entries["web-research"].available_for(frozenset({"url_read"}))
    assert not entries["web-research"].available_for(frozenset({"weather_forecast"}))
    assert entries["comparison-advice"].available_for(frozenset())


def test_read_skill_returns_body_without_frontmatter_and_lists_references(tmp_path):
    directory = _write_skill(tmp_path, "trip-planning", "name: trip-planning\ndescription: Plan a trip.")
    references = directory / "references"
    references.mkdir()
    (references / "checklist.md").write_text("Packing checklist\n", encoding="utf-8")
    (references / "image.png").write_bytes(b"\x89PNG")
    (references / "nested").mkdir()
    (references / "nested" / "deep.md").write_text("too deep\n", encoding="utf-8")
    (references / "linked.md").symlink_to(references / "checklist.md")
    entry = discover_skills(tmp_path)[0]

    document = read_skill(entry)

    assert document.content == "Do the work step by step.\n"
    assert "name:" not in document.content
    assert len(document.content_sha256) == 64
    assert document.reference_files == ("references/checklist.md",)
    assert read_skill_reference(entry, "references/checklist.md").content == "Packing checklist\n"


@pytest.mark.parametrize(
    "relative_path",
    ["../trip-planning/SKILL.md", "SKILL.md", "references/../SKILL.md", "references/nested/deep.md", "/etc/hosts"],
)
def test_reference_reads_are_limited_to_listed_files(tmp_path, relative_path):
    directory = _write_skill(tmp_path, "trip-planning", "name: trip-planning\ndescription: Plan a trip.")
    (directory / "references").mkdir()
    (directory / "references" / "nested").mkdir()
    (directory / "references" / "nested" / "deep.md").write_text("too deep\n", encoding="utf-8")
    entry = discover_skills(tmp_path)[0]

    with pytest.raises(SkillFileError):
        read_skill_reference(entry, relative_path)


@pytest.mark.parametrize(
    "document",
    ["no frontmatter\n", "---\nname: x\n", "---\n- a list\n---\nbody\n"],
)
def test_parse_rejects_malformed_documents(document):
    with pytest.raises(SkillFileError):
        parse_skill_document(document)


def test_bundled_skills_all_parse():
    root = Path(__file__).resolve().parents[3] / "app" / "ai" / "skills"
    bundled = {path.parent.name for path in root.glob("*/SKILL.md")}

    assert {entry.name for entry in discover_skills()} == bundled
