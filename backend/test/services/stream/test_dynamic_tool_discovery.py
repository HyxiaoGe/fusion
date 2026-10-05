"""授权 MCP 工具过多时改由 tool_search 按需加载。

触发只看规模（数量或 schema 体积），不看用户意图；内置工具始终直接公告。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.stream.agent_loop_request_prep import (
    MAX_DIRECT_MCP_SCHEMA_CHARS,
    MAX_DIRECT_MCP_TOOLS,
    build_agent_loop_call_config,
)
from app.services.stream.dynamic_tool_discovery import (
    TOOL_SEARCH_NAME,
    DynamicToolDiscoverySession,
    ToolSearchHandler,
    attach_session_runtime,
    build_discovery_entries,
)

CAPABILITIES = {"functionCalling": True, "searchCapable": True, "agentTools": True}


def _tool(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"{name} description",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _names(tools: list[dict]) -> list[str]:
    return [tool["function"]["name"] for tool in tools]


def _config(mcp_count: int, *, description: str = "", options: dict | None = None):
    mcp_tools = [_tool(f"mcp_docs_tool_{index}", description) for index in range(mcp_count)]
    additional = [_tool("weather_forecast"), *mcp_tools]
    handlers = {_names([tool])[0]: object() for tool in additional}
    bindings = [{"alias": name, "server_id": "srv"} for name in handlers if name.startswith("mcp_")]
    return build_agent_loop_call_config(
        provider="qwen",
        options=options or {},
        capabilities=CAPABILITIES,
        additional_tools=additional,
        dynamic_tool_handlers=handlers,
        tool_bindings=bindings,
    )


def test_few_mcp_tools_are_announced_directly():
    config = _config(MAX_DIRECT_MCP_TOOLS)

    assert config.tool_discovery is None
    assert config.capability_resolution.deferred_tool_names == ()
    assert "mcp_docs_tool_0" in _names(config.call_kwargs["tools"])
    assert TOOL_SEARCH_NAME not in _names(config.call_kwargs["tools"])


def test_too_many_mcp_tools_are_deferred_behind_tool_search():
    config = _config(MAX_DIRECT_MCP_TOOLS + 1)
    tool_names = _names(config.call_kwargs["tools"])

    assert config.tool_discovery is not None
    assert len(config.capability_resolution.deferred_tool_names) == MAX_DIRECT_MCP_TOOLS + 1
    assert not any(name.startswith("mcp_") for name in tool_names)
    assert {"web_search", "url_read", "weather_forecast", TOOL_SEARCH_NAME} <= set(tool_names)
    assert TOOL_SEARCH_NAME in config.control_tool_names
    assert TOOL_SEARCH_NAME in config.dynamic_tool_handlers


def test_large_mcp_schemas_are_deferred_even_when_few():
    config = _config(2, description="x" * (MAX_DIRECT_MCP_SCHEMA_CHARS // 2 + 1))

    assert config.tool_discovery is not None
    assert config.capability_resolution.deferred_tool_names == ("mcp_docs_tool_0", "mcp_docs_tool_1")


def test_deep_research_never_defers_and_never_announces_mcp():
    config = _config(
        MAX_DIRECT_MCP_TOOLS + 1,
        options={"task_mode": "deep_research", "use_web_search": True},
    )

    assert config.tool_discovery is None
    assert _names(config.call_kwargs["tools"])[:2] == ["web_search", "url_read"]
    assert not any(name.startswith("mcp_") for name in _names(config.call_kwargs["tools"]))


def _session(*names: str) -> DynamicToolDiscoverySession:
    schemas = {name: _tool(name, f"{name} looks up docs") for name in names}
    handlers = {name: object() for name in names}
    session = DynamicToolDiscoverySession(
        authorized=build_discovery_entries(
            schemas_by_name=schemas,
            handlers_by_name=handlers,
            bindings=[{"alias": name} for name in names],
            authorized_names=list(names),
        )
    )
    attach_session_runtime(session, call_kwargs={"tools": []}, handlers={}, bindings=[])
    return session


def test_select_loads_tool_into_next_round():
    session = _session("mcp_docs_a", "mcp_docs_b")

    result = asyncio.run(ToolSearchHandler(session).execute({"query": "select:mcp_docs_b"}))

    assert result.data["promoted_names"] == ["mcp_docs_b"]
    assert _names(session.call_kwargs["tools"]) == ["mcp_docs_b"]
    assert "mcp_docs_b" in session.handlers
    assert session.bindings == [{"alias": "mcp_docs_b"}]
    again = asyncio.run(ToolSearchHandler(session).execute({"query": "select:mcp_docs_b"}))
    assert again.data["promoted_names"] == []
    assert _names(session.call_kwargs["tools"]) == ["mcp_docs_b"]


def test_unmatched_query_lists_catalog_without_loading():
    session = _session("mcp_docs_a")

    result = asyncio.run(ToolSearchHandler(session).execute({"query": "weather in paris"}))

    assert result.data["mode"] == "list"
    assert [item["name"] for item in result.data["catalog"]] == ["mcp_docs_a"]
    assert session.call_kwargs["tools"] == []


@pytest.mark.parametrize(
    ("name", "reason"),
    [("mcp_docs_a", "tool_authorized_but_not_loaded"), ("mcp_other", "tool_not_authorized")],
)
def test_calling_an_unloaded_tool_is_intercepted(name, reason):
    payload = _session("mcp_docs_a").format_intercept(name)

    assert payload["status"] == "not_executed"
    assert payload["reason"] == reason
    json.dumps(payload)


def test_catalog_prompt_lists_only_deferred_tools():
    prompt = _session("mcp_docs_a").catalog_prompt()

    assert "<deferred-tools>" in prompt
    assert "- mcp_docs_a: mcp_docs_a looks up docs" in prompt
