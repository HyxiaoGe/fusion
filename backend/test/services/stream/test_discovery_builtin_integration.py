"""覆盖生产入口未显式传入内置 handler 时的目录与授权边界。"""

import asyncio

import pytest

from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
from app.services.stream.dynamic_tool_discovery import DynamicToolDiscoveryUnsupportedError
from app.services.tool_handlers import get_handler


def _config(**kwargs):
    params = {
        "provider": "openai",
        "options": {"dynamic_tool_discovery": True, "plan_mode": "off"},
        "capabilities": {"functionCalling": True, "searchCapable": True, "agentTools": True},
        "original_message": "请搜索官方公告并读取原文",
    }
    params.update(kwargs)
    return build_agent_loop_call_config(**params)


def _schema(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


@pytest.mark.parametrize("with_mcp", [False, True])
def test_production_builtins_can_be_discovered_and_loaded(with_mcp):
    mcp_handler = object()
    kwargs = {}
    if with_mcp:
        kwargs = {
            "additional_tools": [_schema("mcp_allowed"), _schema("mcp_not_authorized")],
            "dynamic_tool_handlers": {"mcp_allowed": mcp_handler, "mcp_not_authorized": object()},
            "authorized_tool_names": ["mcp_allowed", "mcp_missing_handler"],
        }
    config = _config(**kwargs)
    session = config.tool_discovery
    expected = {"web_search", "url_read"} | ({"mcp_allowed"} if with_mcp else set())
    assert set(session.catalog_names()) == expected
    assert [tool["function"]["name"] for tool in config.call_kwargs["tools"]] == ["tool_search"]
    discovery = config.dynamic_tool_handlers["tool_search"]
    asyncio.run(discovery.execute({"query": "list"}))
    assert session.loaded_names == {"tool_search"}
    asyncio.run(discovery.execute({"query": "select:web_search,url_read"}))
    assert session.loaded_names == {"tool_search", "web_search", "url_read"}
    assert {tool["function"]["name"] for tool in config.call_kwargs["tools"]} == {
        "tool_search",
        "web_search",
        "url_read",
    }
    for name in ("web_search", "url_read"):
        assert config.dynamic_tool_handlers[name] is get_handler(name)
        assert get_handler(name) is not None
    if with_mcp:
        assert session.authorized["mcp_allowed"].handler is mcp_handler


def test_explicit_builtin_handler_is_preserved_without_mutating_input():
    override = object()
    handlers = {"web_search": override}
    config = _config(dynamic_tool_handlers=handlers)
    assert config.tool_discovery.authorized["web_search"].handler is override
    assert config.tool_discovery.authorized["url_read"].handler is get_handler("url_read")
    assert handlers == {"web_search": override}


@pytest.mark.parametrize("agent_tools", [False, True])
def test_upstream_filtered_mcp_catalog_without_explicit_authorization_list(agent_tools):
    config = _config(
        capabilities={"functionCalling": True, "searchCapable": True, "agentTools": agent_tools},
        additional_tools=[_schema("mcp_allowed")],
        dynamic_tool_handlers={"mcp_allowed": object()},
    )
    expected = {"web_search", "url_read"} | ({"mcp_allowed"} if agent_tools else set())
    assert set(config.tool_discovery.catalog_names()) == expected


def test_builtin_without_registered_handler_stays_unavailable(monkeypatch):
    monkeypatch.setattr("app.services.tool_handlers.get_handler", lambda _name: None)
    assert _config().tool_discovery.catalog_names() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"options": {"dynamic_tool_discovery": True, "disable_tools": True}},
        {"options": {"dynamic_tool_discovery": True, "knowledge_grounded": True}},
        {"capabilities": {"functionCalling": False, "searchCapable": True}},
        {"capabilities": {"functionCalling": True, "searchCapable": False}},
    ],
)
def test_unavailable_tool_capability_still_rejects_discovery(overrides):
    with pytest.raises(DynamicToolDiscoveryUnsupportedError):
        _config(**overrides)


def test_legacy_path_does_not_resolve_builtin_handlers_early(monkeypatch):
    def unexpected(_name):
        raise AssertionError("旧路径不应提前解析内置 handler")

    monkeypatch.setattr("app.services.tool_handlers.get_handler", unexpected)
    config = _config(options={"plan_mode": "off"}, original_message="你好")
    assert not config.dynamic_tool_discovery
    assert config.tool_discovery is None
