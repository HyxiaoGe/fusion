"""Fusion 代码内置 Skills（Agent Skills 开放标准）。"""

from app.ai.skills.catalog import (
    SkillDocument,
    SkillEntry,
    SkillFileError,
    discover_skills,
    read_skill,
    read_skill_reference,
)

__all__ = [
    "SkillDocument",
    "SkillEntry",
    "SkillFileError",
    "discover_skills",
    "read_skill",
    "read_skill_reference",
]
