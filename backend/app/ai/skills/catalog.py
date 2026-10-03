"""按 Agent Skills 开放标准（agentskills.io）读取代码内置 Skill。

目录约定：``app/ai/skills/<name>/SKILL.md``，可选 ``references/`` 下的附属文件。
启动后只解析 frontmatter 组成目录；正文与附属文件由模型调用 load_skill 时才读取。
单个 Skill 不合法时跳过并告警，不影响其他 Skill。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import app_logger as logger
from app.utils.run_capability_contract import CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER

SKILLS_ROOT = Path(__file__).resolve().parent
SKILL_FILE_NAME = "SKILL.md"
REFERENCES_DIR = "references"
# Fusion 自定义 metadata：本 Run 授权了其中任一工具时才在目录中列出；缺省表示不依赖工具。
TOOLS_METADATA_KEY = "fusion-tools"
MAX_SKILL_FILE_BYTES = 64 * 1024
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REFERENCE_SUFFIXES = frozenset({".md", ".txt"})
_KNOWN_TOOL_NAMES = frozenset(CAPABILITY_CANONICAL_EXTERNAL_TOOL_ORDER)


class SkillFileError(ValueError):
    """Skill 文件不存在、越界或不合法；消息可直接回给模型，不含绝对路径。"""


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    tool_names: frozenset[str]
    directory: Path

    def available_for(self, authorized_tool_names: frozenset[str]) -> bool:
        return not self.tool_names or bool(self.tool_names & authorized_tool_names)


@dataclass(frozen=True)
class SkillDocument:
    name: str
    content: str
    content_sha256: str
    reference_files: tuple[str, ...]


def discover_skills(root: Path | None = None) -> tuple[SkillEntry, ...]:
    return _discover_skills((root or SKILLS_ROOT).resolve())


@lru_cache(maxsize=4)
def _discover_skills(root: Path) -> tuple[SkillEntry, ...]:
    entries: list[SkillEntry] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if not (directory / SKILL_FILE_NAME).exists():
            continue
        try:
            entries.append(_load_entry(directory))
        except (OSError, UnicodeError, SkillFileError, yaml.YAMLError) as error:
            logger.warning("Skill 已跳过: dir=%s error=%s", directory.name, error)
    return tuple(entries)


def read_skill(entry: SkillEntry) -> SkillDocument:
    _frontmatter, body = parse_skill_document(_read_text(entry.directory / SKILL_FILE_NAME))
    return SkillDocument(
        name=entry.name,
        content=body,
        content_sha256=_sha256(body),
        reference_files=_reference_files(entry.directory),
    )


def read_skill_reference(entry: SkillEntry, relative_path: str) -> SkillDocument:
    """只允许读取 references/ 下一层的文本文件，引用深度与开放标准一致。"""

    if relative_path not in _reference_files(entry.directory):
        raise SkillFileError(f"File not found in skill {entry.name}: {relative_path}")
    content = _read_text(entry.directory / relative_path)
    return SkillDocument(
        name=entry.name,
        content=content,
        content_sha256=_sha256(content),
        reference_files=(),
    )


def parse_skill_document(document: str) -> tuple[dict[str, Any], str]:
    lines = document.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillFileError("SKILL.md must start with YAML frontmatter")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise SkillFileError("SKILL.md frontmatter is not closed") from error
    frontmatter = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(frontmatter, dict):
        raise SkillFileError("SKILL.md frontmatter must be a mapping")
    body = "\n".join(lines[closing + 1 :]).strip("\n") + "\n"
    if not body.strip():
        raise SkillFileError("SKILL.md body is empty")
    return frontmatter, body


def _load_entry(directory: Path) -> SkillEntry:
    frontmatter, _body = parse_skill_document(_read_text(directory / SKILL_FILE_NAME))
    name = frontmatter.get("name")
    if (
        not isinstance(name, str)
        or len(name) > MAX_NAME_LENGTH
        or _NAME_RE.fullmatch(name) is None
        or name != directory.name
    ):
        raise SkillFileError("name must be lowercase-hyphenated and match the directory")
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > MAX_DESCRIPTION_LENGTH:
        raise SkillFileError("description must be 1-1024 characters")
    metadata = frontmatter.get("metadata") or {}
    if not isinstance(metadata, dict) or not all(isinstance(value, str) for value in metadata.values()):
        raise SkillFileError("metadata must map strings to strings")
    tool_names = frozenset(str(metadata.get(TOOLS_METADATA_KEY, "")).split())
    if not tool_names <= _KNOWN_TOOL_NAMES:
        raise SkillFileError(f"{TOOLS_METADATA_KEY} lists unknown tools")
    return SkillEntry(
        name=name,
        description=" ".join(description.split()),
        tool_names=tool_names,
        directory=directory,
    )


def _reference_files(directory: Path) -> tuple[str, ...]:
    references = directory / REFERENCES_DIR
    if not references.is_dir() or references.is_symlink():
        return ()
    return tuple(
        f"{REFERENCES_DIR}/{path.name}"
        for path in sorted(references.iterdir())
        if path.is_file() and not path.is_symlink() and path.suffix in _REFERENCE_SUFFIXES
    )


def _read_text(path: Path) -> str:
    if path.is_symlink():
        raise SkillFileError("Skill files must not be symlinks")
    raw = path.read_bytes()
    if len(raw) > MAX_SKILL_FILE_BYTES:
        raise SkillFileError("Skill file exceeds the size limit")
    return raw.decode("utf-8")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
