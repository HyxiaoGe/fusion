"""授权目录内的动态工具发现（实验 opt-in）。

候选路径不调用包分类器，也不伪造能力包去绕过旧校验。发现仅限本次 Run
已授权且具备 schema/handler 的目录；扩容时同步更新 schema、handler、
MCP binding 与计划允许集。默认产品路径保持关闭。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.stream.run_capability_request_signals import _resolve_network_scope
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

TOOL_SEARCH_NAME = "tool_search"
MAX_SEARCH_RESULTS = 8
NETWORK_TOOL_NAMES = frozenset({"web_search", "url_read"})
PRODUCT_EVIDENCE_TOOL_NAMES = frozenset(
    {
        "weather_forecast",
        "local_place_search",
        "route_compare",
        "search_flights",
        "search_trains",
        "web_search",
        "url_read",
    }
)
_PAGE_RE = re.compile(r"^(?:list|page)(?::(\d+))?$", re.IGNORECASE)
_UNSUPPORTED_SCENES = ("skill", "deep_research", "continuation")


def _tool_definition_name(tool: dict) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def is_dynamic_tool_discovery_enabled(options: Mapping[str, Any] | None) -> bool:
    return bool(options) and options.get("dynamic_tool_discovery") is True


def resolve_discovery_network_denials(original_message: str | None) -> tuple[bool, bool, bool]:
    """复用现有否定信号解析，不重写自然语言授权规则。"""

    _routing, web_denied, url_denied, all_denied = _resolve_network_scope(original_message or "")
    return web_denied, url_denied, all_denied


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


@dataclass(frozen=True)
class AuthorizedToolEntry:
    name: str
    summary: str
    schema: dict[str, Any]
    handler: Any
    binding: dict[str, Any] | None = None


@dataclass
class DynamicToolDiscoverySession:
    """单次 Run 的授权目录、已加载集合与发现事件。"""

    authorized: dict[str, AuthorizedToolEntry]
    denied_names: frozenset[str] = field(default_factory=frozenset)
    loaded_names: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    unsupported_scenes: tuple[str, ...] = _UNSUPPORTED_SCENES
    call_kwargs: dict[str, Any] | None = None
    handlers: dict[str, Any] | None = None
    bindings: list[dict[str, Any]] | None = None
    plan_mode: str = "off"
    budget_identities: dict[str, int] = field(default_factory=dict)
    plan_coordinator: Any = None

    def catalog_names(self) -> list[str]:
        return [name for name in self.authorized if name not in self.denied_names]

    def catalog_prompt(self) -> str:
        lines = [
            "You can discover authorized tools with `tool_search`.",
            "Only names listed below are in this run's authorized catalog.",
            "Call `tool_search` before using a deferred tool; unmatched queries list the catalog.",
            "<available-authorized-tools>",
        ]
        for name in self.catalog_names():
            entry = self.authorized[name]
            lines.append(f"- {name}: {entry.summary}")
        lines.append("</available-authorized-tools>")
        return "\n".join(lines)

    def record(self, **event: Any) -> None:
        self.events.append(dict(event))

    def is_authorized(self, name: str) -> bool:
        return name in self.authorized and name not in self.denied_names

    def is_loaded(self, name: str) -> bool:
        return name in self.loaded_names

    def search(self, query: str) -> tuple[list[AuthorizedToolEntry], str]:
        catalog = [self.authorized[name] for name in self.catalog_names()]
        stripped = (query or "").strip()
        if not stripped:
            return catalog, "list"
        page_match = _PAGE_RE.fullmatch(stripped)
        if page_match:
            page = int(page_match.group(1) or "0")
            start = max(0, page) * MAX_SEARCH_RESULTS
            return catalog[start : start + MAX_SEARCH_RESULTS], "list"
        if stripped.startswith("select:"):
            wanted = {item.strip() for item in stripped[7:].split(",") if item.strip()}
            matched = [entry for entry in catalog if entry.name in wanted]
            missing = sorted(name for name in wanted if name not in {entry.name for entry in matched})
            denied = sorted(name for name in wanted if name in self.denied_names or name not in self.authorized)
            if denied:
                self.record(kind="discover_denied", names=denied, query=stripped)
            if missing and not matched:
                return catalog[:MAX_SEARCH_RESULTS], "list"
            return matched, "select"
        if stripped.startswith("+"):
            parts = stripped[1:].split(None, 1)
            if not parts:
                return catalog[:MAX_SEARCH_RESULTS], "list"
            required = parts[0].lower()
            candidates = [entry for entry in catalog if required in entry.name.lower()]
            if len(parts) > 1:
                regex = _compile_catalog_regex(parts[1])
                candidates.sort(
                    key=lambda entry: len(regex.findall(f"{entry.name} {entry.summary}")),
                    reverse=True,
                )
            return candidates[:MAX_SEARCH_RESULTS], "search"
        regex = _compile_catalog_regex(stripped)
        scored: list[tuple[int, AuthorizedToolEntry]] = []
        for entry in catalog:
            haystack = f"{entry.name} {entry.summary}"
            if regex.search(haystack):
                scored.append((2 if regex.search(entry.name) else 1, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        matched = [entry for _, entry in scored[:MAX_SEARCH_RESULTS]]
        if not matched:
            return catalog[:MAX_SEARCH_RESULTS], "list"
        return matched, "search"

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
        if promoted:
            self._sync_plan_tool_schema()
            if self.plan_coordinator is not None:
                expand_plan_allowed_tools(self.plan_coordinator, promoted)
        return promoted

    def format_intercept(self, name: str) -> dict[str, Any]:
        if self.is_authorized(name) and not self.is_loaded(name):
            payload = {
                "status": "not_executed",
                "reason": "tool_authorized_but_not_loaded",
                "tool_name": name,
                "message": (
                    f"`{name}` is authorized for this run but not loaded. "
                    f"Call `{TOOL_SEARCH_NAME}` with query `select:{name}` to fetch its schema, then retry."
                ),
            }
            self.record(kind="unloaded_intercept", name=name)
            return payload
        payload = {
            "status": "not_executed",
            "reason": "tool_not_authorized",
            "tool_name": name,
            "message": (
                f"`{name}` is not in this run's authorized catalog and cannot be activated. "
                f"Call `{TOOL_SEARCH_NAME}` with an empty query to list allowed tools."
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
        schema = dict(entry.schema)
        if self.plan_mode != "off":
            from app.services.stream.agent_loop_request_prep import _with_plan_item_binding

            schema = _with_plan_item_binding(schema, required=self.plan_mode == "on")
        tools.append(schema)
        self.call_kwargs["tools"] = tools
        self.call_kwargs["tool_choice"] = "auto"

    def _sync_plan_tool_schema(self) -> None:
        if self.call_kwargs is None or self.plan_mode == "off":
            return
        from app.services.stream.agent_loop_request_prep import build_update_plan_tool

        allowed = [name for name in self.loaded_names if name != TOOL_SEARCH_NAME]
        tools = []
        for tool in self.call_kwargs.get("tools") or []:
            if _tool_definition_name(tool) == "update_plan":
                tools.append(build_update_plan_tool(allowed))
            else:
                tools.append(tool)
        self.call_kwargs["tools"] = tools

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
                "message": "No exclusive match; listing the current authorized catalog.",
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
                "Fetch schemas for authorized deferred tools. "
                "Query forms: keyword, `select:name1,name2`, `+name extra`, empty/`list`/`page:N` to browse."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query or select:/list/page:N",
                    }
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
        summary = ""
        if isinstance(function, dict):
            summary = str(function.get("description") or name)
        else:
            summary = name
        entries[name] = AuthorizedToolEntry(
            name=name,
            summary=summary.split("\n", 1)[0][:180],
            schema=schema,
            handler=handler,
            binding=bindings_by_alias.get(name),
        )
    return entries


def denied_network_tool_names(original_message: str | None) -> frozenset[str]:
    web_denied, url_denied, all_denied = resolve_discovery_network_denials(original_message)
    denied: set[str] = set()
    if all_denied or web_denied:
        denied.add("web_search")
    if all_denied or url_denied:
        denied.add("url_read")
    return frozenset(denied)


def attach_session_runtime(
    session: DynamicToolDiscoverySession,
    *,
    call_kwargs: dict[str, Any],
    handlers: dict[str, Any],
    bindings: list[dict[str, Any]],
    plan_mode: str,
) -> None:
    session.call_kwargs = call_kwargs
    session.handlers = handlers
    session.bindings = bindings
    session.plan_mode = plan_mode
    session.loaded_names.add(TOOL_SEARCH_NAME)
    session.handlers[TOOL_SEARCH_NAME] = ToolSearchHandler(session)
    for name, handler in handlers.items():
        session._record_budget_identity(handler)


def expand_plan_allowed_tools(coordinator: Any, names: Iterable[str]) -> None:
    extra = frozenset(name for name in names if name and name != TOOL_SEARCH_NAME)
    current = getattr(coordinator, "allowed_tool_names", None)
    if current is None:
        coordinator.allowed_tool_names = extra
    else:
        coordinator.allowed_tool_names = frozenset(current) | extra


def discovery_requires_external_evidence(session: DynamicToolDiscoverySession | None) -> bool:
    if session is None:
        return False
    return any(name in PRODUCT_EVIDENCE_TOOL_NAMES for name in session.authorized)
