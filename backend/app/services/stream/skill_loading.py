"""Run 级 Skill 按需加载：目录进 system prompt，模型调用 load_skill 读取正文。

Skill 只提供做事方法，不授予工具：目录只列出依赖工具已在本 Run 授权范围内的
Skill，工具开放仍完全由能力包决定。load_skill 与文档工具一样不属于计划步骤，
不要求计划绑定，也不占外部工具调用预算。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.ai.skills import SkillEntry, SkillFileError, discover_skills, read_skill, read_skill_reference
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

LOAD_SKILL_TOOL_NAME = "load_skill"
MAX_SKILL_LOADS_PER_RUN = 5


@dataclass
class SkillSession:
    entries: dict[str, SkillEntry]
    loaded: dict[str, str] = field(default_factory=dict)
    attempts: int = 0

    def catalog_prompt(self) -> str:
        lines = render_runtime_prompt("skills.catalog_preamble").splitlines()
        lines.append("<available-skills>")
        lines.extend(f"- {name}: {entry.description}" for name, entry in self.entries.items())
        lines.append("</available-skills>")
        return "\n".join(lines)

    @property
    def budget_exhausted(self) -> bool:
        return self.attempts >= MAX_SKILL_LOADS_PER_RUN


def build_skill_session(authorized_tool_names: Iterable[str]) -> SkillSession | None:
    authorized = frozenset(authorized_tool_names)
    entries = {entry.name: entry for entry in discover_skills() if entry.available_for(authorized)}
    return SkillSession(entries=entries) if entries else None


def build_load_skill_schema(session: SkillSession) -> dict:
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL_TOOL_NAME,
            "description": render_runtime_prompt("skills.tool_description"),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": list(session.entries)},
                    "file": {
                        "type": "string",
                        "description": "Optional reference file path listed by the loaded skill, e.g. references/x.md",
                    },
                },
                "required": ["name"],
            },
        },
    }


class LoadSkillHandler(BaseToolHandler):
    supports_automatic_retry = False

    def __init__(self, session: SkillSession) -> None:
        self._session = session

    @property
    def tool_name(self) -> str:
        return LOAD_SKILL_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return LOAD_SKILL_TOOL_NAME

    async def is_run_budget_exhausted(self) -> bool:
        return self._session.budget_exhausted

    async def execute(self, args: dict) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        name = str(args.get("name") or "")
        file = str(args.get("file") or "").strip() or None
        if self._session.budget_exhausted:
            return _failed("skill_load_limit_reached", "Skill load limit for this run reached.")
        self._session.attempts += 1
        entry = self._session.entries.get(name)
        if entry is None:
            return _failed(
                "unknown_skill",
                f"Unknown skill. Available: {', '.join(self._session.entries)}",
            )
        key = f"{name}/{file}" if file else name
        if key in self._session.loaded:
            return ToolResult(
                status="success",
                data={
                    "name": name,
                    "file": file,
                    "content_sha256": self._session.loaded[key],
                    "already_loaded": True,
                },
            )
        try:
            document = read_skill_reference(entry, file) if file else read_skill(entry)
        except (OSError, UnicodeError, SkillFileError) as error:
            message = str(error) if isinstance(error, SkillFileError) else "Skill file could not be read."
            return _failed("skill_file_unavailable", message)
        self._session.loaded[key] = document.content_sha256
        return ToolResult(
            status="success",
            data={
                "name": name,
                "file": file,
                "content_sha256": document.content_sha256,
                "content": document.content,
                "reference_files": list(document.reference_files),
            },
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        return None

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        data = result.data
        if result.status != "success":
            return str(data.get("message") or "Skill could not be loaded.")
        label = f"{data['name']}/{data['file']}" if data.get("file") else data["name"]
        if data.get("already_loaded"):
            return f"Skill {label} is already loaded earlier in this run; follow the instructions above."
        parts = [f'<skill name="{label}">', data["content"].rstrip(), "</skill>"]
        if data.get("reference_files"):
            parts.append("Reference files: " + ", ".join(data["reference_files"]))
        return "\n".join(parts)

    def _build_result_summary(self, result: ToolResult) -> dict:
        data = result.data
        return {
            "kind": LOAD_SKILL_TOOL_NAME,
            "truncated": False,
            "skill": data.get("name"),
            "file": data.get("file"),
        }


def _failed(reason: str, message: str) -> ToolResult:
    return ToolResult(status="failed", data={"reason": reason, "message": message}, error_message=message)
