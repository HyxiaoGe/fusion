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

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

TOOL_SEARCH_NAME = "tool_search"
MAX_SEARCH_RESULTS = 8
NETWORK_KIND_LOCAL_READONLY = "local_readonly"
NETWORK_KIND_SEARCH = "search"
NETWORK_KIND_URL = "url"
NETWORK_KIND_PRODUCT_QUERY = "product_query"
NETWORK_KIND_UNKNOWN_NETWORK = "unknown_network"
KNOWN_NETWORK_KINDS: dict[str, str] = {
    "web_search": NETWORK_KIND_SEARCH,
    "url_read": NETWORK_KIND_URL,
    "weather_forecast": NETWORK_KIND_PRODUCT_QUERY,
    "local_place_search": NETWORK_KIND_PRODUCT_QUERY,
    "route_compare": NETWORK_KIND_PRODUCT_QUERY,
    "search_flights": NETWORK_KIND_PRODUCT_QUERY,
    "search_trains": NETWORK_KIND_PRODUCT_QUERY,
    "mcp_readonly_probe": NETWORK_KIND_LOCAL_READONLY,
}
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
NETWORK_POLICIES = frozenset({"allow", "no_web_search", "no_url_read", "no_network"})
CATALOG_EVIDENCE_ADAPTER_NOTE = (
    "requires_catalog_evidence is a conservative experiment adapter. "
    "Authorized catalog presence does not by itself create an evidence "
    "obligation for ordinary greeting or identity replies."
)


class DynamicToolDiscoveryUnsupportedError(ValueError):
    """opt-in 发现路径明确拒绝尚未覆盖的场景，禁止静默回退旧分类。"""

    def __init__(self, scene: str) -> None:
        self.scene = scene
        super().__init__(f"dynamic_tool_discovery does not support {scene}")


def _tool_definition_name(tool: dict) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def is_dynamic_tool_discovery_enabled(options: Mapping[str, Any] | None) -> bool:
    return bool(options) and options.get("dynamic_tool_discovery") is True


def infer_network_kind(name: str, *, binding: dict[str, Any] | None = None) -> str:
    if name in KNOWN_NETWORK_KINDS:
        return KNOWN_NETWORK_KINDS[name]
    if binding or name.startswith("mcp_"):
        return NETWORK_KIND_UNKNOWN_NETWORK
    return NETWORK_KIND_UNKNOWN_NETWORK


def network_kind_is_denied(kind: str, network_policy: str) -> bool:
    """未知网络工具在限制联网能力时按保守边界处理。"""

    if network_policy == "no_network":
        return kind != NETWORK_KIND_LOCAL_READONLY
    if network_policy == "no_web_search":
        return kind in {NETWORK_KIND_SEARCH, NETWORK_KIND_UNKNOWN_NETWORK}
    if network_policy == "no_url_read":
        return kind in {NETWORK_KIND_URL, NETWORK_KIND_UNKNOWN_NETWORK}
    return False


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
    network_kind: str = NETWORK_KIND_UNKNOWN_NETWORK


@dataclass(frozen=True)
class DiscoveryExperimentContext:
    """实验 Run 的显式上下文；不是能力包，不得写入 TrajectoryCapabilityResolution。"""

    enabled: bool = True
    include_current_date: bool = True
    effective_plan_mode: str = "off"
    network_boundary_required: bool = False
    requires_catalog_evidence: bool = False
    catalog_evidence_note: str = CATALOG_EVIDENCE_ADAPTER_NOTE
    unsupported_scenes: tuple[str, ...] = _UNSUPPORTED_SCENES
    announced_tools: tuple[str, ...] = ()
    authorized_tool_names: tuple[str, ...] = ()
    external_tool_names: tuple[str, ...] = ()
    loaded_skills: tuple[Any, ...] = ()
    skill_resolution: Any = None
    package_id: None = None


@dataclass
class DynamicToolDiscoverySession:
    """单次 Run 的授权目录、已加载集合与发现事件。"""

    authorized: dict[str, AuthorizedToolEntry]
    denied_names: frozenset[str] = field(default_factory=frozenset)
    declared_network_policy: str | None = None
    declared_denied_tool_names: frozenset[str] | None = None
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
        lines = render_runtime_prompt("dynamic_tool_discovery.catalog_preamble").splitlines()
        lines.append("<available-authorized-tools>")
        for name in self.catalog_names():
            entry = self.authorized[name]
            lines.append(f"- {name}: {entry.summary}")
        lines.append("</available-authorized-tools>")
        return "\n".join(lines)

    def record(self, **event: Any) -> None:
        self.events.append(dict(event))

    def apply_declared_constraints(self, network_policy: object, denied_tool_names: object) -> bool:
        """首次发现必须声明约束；后续调用不得放宽或修改已冻结的目录。"""

        if (
            not isinstance(network_policy, str)
            or network_policy not in NETWORK_POLICIES
            or not isinstance(denied_tool_names, list)
        ):
            return False
        if (
            len(denied_tool_names) > 8
            or any(not isinstance(name, str) or name not in self.authorized for name in denied_tool_names)
            or len(set(denied_tool_names)) != len(denied_tool_names)
        ):
            return False
        declared_names = frozenset(denied_tool_names)
        if self.declared_network_policy is not None:
            return network_policy == self.declared_network_policy and declared_names == self.declared_denied_tool_names
        self.declared_network_policy = network_policy
        self.declared_denied_tool_names = declared_names
        self.denied_names = frozenset(
            name
            for name, entry in self.authorized.items()
            if name in declared_names or network_kind_is_denied(entry.network_kind, network_policy)
        )
        self.record(
            kind="tool_constraints_declared", network_policy=network_policy, denied_names=sorted(self.denied_names)
        )
        return True

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
            required = parts[0].casefold()
            candidates = [entry for entry in catalog if required in entry.name.casefold()]
            if len(parts) > 1:
                extra = parts[1]
                candidates = [entry for entry in candidates if _literal_catalog_match(extra, entry)]
            if not candidates:
                return catalog[:MAX_SEARCH_RESULTS], "list"
            return candidates[:MAX_SEARCH_RESULTS], "search"
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
        if not isinstance(args, dict):
            args = {}
        if not self._session.apply_declared_constraints(args.get("network_policy"), args.get("denied_tool_names")):
            self._session.record(kind="tool_constraints_invalid")
            return ToolResult(
                status="failed",
                data={
                    "reason": "invalid_tool_constraints",
                    "message": "Declare valid, unchanged network_policy and denied_tool_names before discovery.",
                },
            )
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
                "network_policy": self._session.declared_network_policy,
            }
        else:
            listing = [{"name": entry.name, "summary": entry.summary} for entry in matched]
            data = {
                "mode": "list",
                "query": query,
                "catalog": listing,
                "message": "No exclusive match; listing the current authorized catalog.",
                "network_policy": self._session.declared_network_policy,
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
                "On the first call declare current-request tool restrictions; repeat the same declaration on later calls. "
                "Query forms: literal keyword, `select:name1,name2`, `+name extra`, empty/`list`/`page:N` to browse."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal search query or select:/list/page:N",
                    },
                    "network_policy": {
                        "type": "string",
                        "enum": ["allow", "no_web_search", "no_url_read", "no_network"],
                        "description": "Scope of the current user's prohibition; no_network denies every external network tool.",
                    },
                    "denied_tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                        "description": "Exact authorized tool names explicitly prohibited for this request; [] if none.",
                    },
                },
                "required": ["query", "network_policy", "denied_tool_names"],
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
        binding = bindings_by_alias.get(name)
        entries[name] = AuthorizedToolEntry(
            name=name,
            summary=summary.split("\n", 1)[0][:180],
            schema=schema,
            handler=handler,
            binding=binding,
            network_kind=infer_network_kind(name, binding=binding),
        )
    return entries


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
    """目录含产品工具不等于当前问候任务有证据义务；默认关闭。"""

    del session
    return False
