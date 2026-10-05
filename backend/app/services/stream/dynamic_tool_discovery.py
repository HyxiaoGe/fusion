"""授权 MCP 工具过多时的按需加载。

MCP 工具数量或 schema 体积超过阈值时，不把它们全部放进每轮工具列表，而是只公告
`tool_search` 和一份简短目录；模型检索到的工具从下一轮起加入工具列表。触发条件只看
规模，不看用户意图。目录仅限本次 Run 已授权且具备 schema/handler 的工具。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

TOOL_SEARCH_NAME = "tool_search"
MAX_SEARCH_RESULTS = 8

_PAGE_RE = re.compile(r"^(?:list|page)(?::(\d+))?$", re.IGNORECASE)


def _tool_definition_name(tool: dict) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _literal_catalog_match(query: str, entry: "AuthorizedToolEntry") -> bool:
    needle = query.casefold()
    haystack = f"{entry.name} {entry.summary}".casefold()
    if needle in haystack:
        return True
    tokens = [token for token in needle.replace(",", " ").split() if token]
    return bool(tokens) and all(token in haystack for token in tokens)


@dataclass(frozen=True)
class AuthorizedToolEntry:
    name: str
    summary: str
    schema: dict[str, Any]
    handler: Any
    binding: dict[str, Any] | None = None


@dataclass
class DynamicToolDiscoverySession:
    """单次 Run 的延迟加载目录、已加载集合与发现事件。"""

    authorized: dict[str, AuthorizedToolEntry]
    loaded_names: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    call_kwargs: dict[str, Any] | None = None
    handlers: dict[str, Any] | None = None
    bindings: list[dict[str, Any]] | None = None
    budget_identities: dict[str, int] = field(default_factory=dict)

    def catalog_names(self) -> list[str]:
        return list(self.authorized)

    def catalog_prompt(self) -> str:
        lines = render_runtime_prompt("dynamic_tool_discovery.catalog_preamble").splitlines()
        lines.append("<deferred-tools>")
        for name in self.catalog_names():
            entry = self.authorized[name]
            lines.append(f"- {name}: {entry.summary}")
        lines.append("</deferred-tools>")
        return "\n".join(lines)

    def record(self, **event: Any) -> None:
        self.events.append(dict(event))

    def is_authorized(self, name: str) -> bool:
        return name in self.authorized

    def is_loaded(self, name: str) -> bool:
        return name in self.loaded_names

    def search(self, query: str) -> tuple[list[AuthorizedToolEntry], str]:
        catalog = [self.authorized[name] for name in self.catalog_names()]
        stripped = (query or "").strip()
        if not stripped:
            return catalog[:MAX_SEARCH_RESULTS], "list"
        page_match = _PAGE_RE.fullmatch(stripped)
        if page_match:
            page = int(page_match.group(1) or "0")
            start = max(0, page) * MAX_SEARCH_RESULTS
            return catalog[start : start + MAX_SEARCH_RESULTS], "list"
        if stripped.startswith("select:"):
            wanted = {item.strip() for item in stripped[7:].split(",") if item.strip()}
            matched = [entry for entry in catalog if entry.name in wanted]
            unknown = sorted(name for name in wanted if name not in self.authorized)
            if unknown:
                self.record(kind="discover_denied", names=unknown, query=stripped)
            if not matched:
                return catalog[:MAX_SEARCH_RESULTS], "list"
            return matched, "select"
        matched = [entry for entry in catalog if _literal_catalog_match(stripped, entry)]
        if not matched:
            return catalog[:MAX_SEARCH_RESULTS], "list"
        return matched[:MAX_SEARCH_RESULTS], "search"

    def promote(self, names: Sequence[str]) -> list[str]:
        promoted: list[str] = []
        for name in names:
            if not self.is_authorized(name):
                self.record(kind="activate_denied", name=name)
                continue
            if name in self.loaded_names:
                self.record(kind="discover_idempotent", name=name)
                continue
            entry = self.authorized[name]
            self._append_schema(entry)
            if self.handlers is not None:
                self.handlers[name] = entry.handler
            if self.bindings is not None and entry.binding:
                if not any(str(item.get("alias")) == name for item in self.bindings):
                    self.bindings.append(entry.binding)
            self.loaded_names.add(name)
            self._record_budget_identity(entry.handler)
            promoted.append(name)
            self.record(kind="promoted", name=name)
        return promoted

    def format_intercept(self, name: str) -> dict[str, Any]:
        if self.is_authorized(name) and not self.is_loaded(name):
            payload = {
                "status": "not_executed",
                "reason": "tool_authorized_but_not_loaded",
                "tool_name": name,
                "message": (
                    f"`{name}` is available but not loaded yet. "
                    f"Call `{TOOL_SEARCH_NAME}` with query `select:{name}` to load it, then retry."
                ),
            }
            self.record(kind="unloaded_intercept", name=name)
            return payload
        payload = {
            "status": "not_executed",
            "reason": "tool_not_authorized",
            "tool_name": name,
            "message": (
                f"`{name}` is not an available tool in this run. "
                f"Call `{TOOL_SEARCH_NAME}` with an empty query to list the deferred tools."
            ),
        }
        self.record(kind="unauthorized_intercept", name=name)
        return payload

    def _append_schema(self, entry: AuthorizedToolEntry) -> None:
        if self.call_kwargs is None:
            return
        tools = list(self.call_kwargs.get("tools") or [])
        existing = {_tool_definition_name(tool) for tool in tools if isinstance(tool, dict)}
        if entry.name in existing:
            return
        tools.append(dict(entry.schema))
        self.call_kwargs["tools"] = tools
        self.call_kwargs["tool_choice"] = "auto"

    def _record_budget_identity(self, handler: Any) -> None:
        controls = getattr(handler, "controls", None) or getattr(handler, "budget", None)
        if controls is None:
            return
        self.budget_identities.setdefault("shared_vendor", id(controls))


class ToolSearchHandler(BaseToolHandler):
    supports_automatic_retry = False

    def __init__(self, session: DynamicToolDiscoverySession) -> None:
        self._session = session

    @property
    def tool_name(self) -> str:
        return TOOL_SEARCH_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "tool_search"

    async def execute(self, args: dict) -> ToolResult:
        if not isinstance(args, dict):
            args = {}
        query = str(args.get("query") or "")
        matched, mode = self._session.search(query)
        if mode in {"search", "select"}:
            promoted = self._session.promote([entry.name for entry in matched])
            schemas = [entry.schema.get("function", entry.schema) for entry in matched]
            data = {
                "mode": mode,
                "query": query,
                "matched_names": [entry.name for entry in matched],
                "promoted_names": promoted,
                "schemas": schemas,
            }
        else:
            listing = [{"name": entry.name, "summary": entry.summary} for entry in matched]
            data = {
                "mode": "list",
                "query": query,
                "catalog": listing,
                "message": "No exclusive match; listing deferred tools.",
            }
            self._session.record(kind="catalog_listed", query=query, count=len(listing))
        return ToolResult(status="success", data=data)

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        return None

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        return json.dumps(result.data, ensure_ascii=False)


def build_tool_search_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH_NAME,
            "description": (
                "Load deferred tools listed in the system prompt so they can be called from the next step. "
                "Query forms: keywords, `select:name1,name2`, or empty/`list`/`page:N` to browse."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords, select:name1,name2, or list/page:N",
                    },
                },
                "required": ["query"],
            },
        },
    }


def build_discovery_entries(
    *,
    schemas_by_name: Mapping[str, dict[str, Any]],
    handlers_by_name: Mapping[str, Any],
    bindings: Sequence[dict[str, Any]] | None = None,
    authorized_names: Sequence[str] | None = None,
) -> dict[str, AuthorizedToolEntry]:
    bindings_by_alias = {
        str(binding.get("alias", "")): binding
        for binding in (bindings or [])
        if isinstance(binding, dict) and binding.get("alias")
    }
    names = list(authorized_names or [])
    if not names:
        names = [name for name in schemas_by_name if name in handlers_by_name]
    entries: dict[str, AuthorizedToolEntry] = {}
    for name in names:
        if name == TOOL_SEARCH_NAME or name == "update_plan":
            continue
        schema = schemas_by_name.get(name)
        handler = handlers_by_name.get(name)
        if not isinstance(name, str) or not schema or handler is None:
            continue
        function = schema.get("function") if isinstance(schema, dict) else None
        summary = str(function.get("description") or name) if isinstance(function, dict) else name
        entries[name] = AuthorizedToolEntry(
            name=name,
            summary=summary.split("\n", 1)[0][:180],
            schema=schema,
            handler=handler,
            binding=bindings_by_alias.get(name),
        )
    return entries


def attach_session_runtime(
    session: DynamicToolDiscoverySession,
    *,
    call_kwargs: dict[str, Any],
    handlers: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> None:
    session.call_kwargs = call_kwargs
    session.handlers = handlers
    session.bindings = bindings
    session.loaded_names.add(TOOL_SEARCH_NAME)
    session.handlers[TOOL_SEARCH_NAME] = ToolSearchHandler(session)
    for handler in handlers.values():
        session._record_budget_identity(handler)
