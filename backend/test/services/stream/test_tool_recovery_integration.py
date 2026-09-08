"""真实路由、工具执行与终态链路的替代工具隔离回归。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.chat import SearchSource
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.mcp.client import McpClientError
from app.services.tool_handlers.url_read import UrlReadHandler
from app.services.tool_handlers.web_search import WebSearchHandler


def _tool_round(tool_id, name, arguments):
    return ("", "", [{"id": tool_id, "name": name, "arguments": arguments}], "tool_calls", None)


async def _run_recovery(*, search_success, answer_suffix="[1]", degrade_read=False):
    # 复用隔离持久化/LLM fixture，执行器、真实处理器和恢复决策均不替换。
    from test.services.stream.test_agent_loop_contract import AgentLoopContractTests
    from test.test_amap_product_tools import build_handler, mcp_payload

    harness = AgentLoopContractTests()
    harness.setUp()
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    weather, remote = build_handler(
        "weather_forecast",
        {
            "maps_geo": [
                mcp_payload(
                    {
                        "geocodes": [
                            {
                                "formatted_address": "香港特别行政区",
                                "province": "香港特别行政区",
                                "city": [],
                                "district": [],
                                "adcode": "810000",
                                "location": "114.17,22.28",
                                "level": "省",
                            }
                        ]
                    }
                )
            ],
            "maps_weather": [
                McpClientError(
                    "tool_error", "MCP 工具执行失败", safe_details={"upstream_message": "API 调用失败：UNKNOWN_ERROR"}
                )
            ],
        },
        weather_cache=cache,
    )
    tools = SimpleNamespace(
        definitions=[
            definition
            for definition in AMAP_PRODUCT_DEFINITIONS
            if definition["function"]["name"] == "weather_forecast"
        ],
        handlers={"weather_forecast": weather, "web_search": WebSearchHandler(), "url_read": UrlReadHandler()},
        audit_bindings=[],
    )
    answer = f"香港明天有骤雨，后天部分时间有阳光，第三天有雷暴。{answer_suffix}"
    rounds = [
        _tool_round("weather-1", "weather_forecast", '{"location":"香港","location_source":"named"}'),
        ("", "天气工具失败，我无法查询。", [], "stop", None),
        _tool_round("search-1", "web_search", '{"query":"香港未来三天天气 香港天文台"}'),
        ("", answer if search_success else "所有查询失败。", [], "stop", None),
    ]
    if degrade_read:
        rounds.insert(
            -1,
            _tool_round("read-1", "url_read", '{"url":"https://www.hko.gov.hk/forecast"}'),
        )
    sources = [
        SearchSource(
            title="香港三天天气",
            url="https://www.hko.gov.hk/forecast",
            description="明天有骤雨，后天部分时间有阳光，第三天有雷暴。",
        )
    ]
    with (
        patch("app.services.tool_handlers.base.BaseToolHandler.log", AsyncMock(return_value=None)),
        patch(
            "app.services.tool_handlers.web_search.search_web",
            AsyncMock(return_value=sources if search_success else []),
        ),
        patch(
            "app.services.tool_handlers.url_read.read_url_with_diagnostics",
            AsyncMock(return_value=SimpleNamespace(result=None, failure=None)),
        ),
    ):
        result = await harness._run_agent_contract(
            rounds=rounds,
            use_real_tool_executor=True,
            dynamic_tool_set=tools,
            capabilities={"functionCalling": True, "agentTools": True, "searchCapable": True},
            user_message="香港未来三天天气怎么样？",
        )
    return result, answer, remote


class ToolRecoveryIntegrationTests(unittest.TestCase):
    def test_failed_domain_then_search_then_degraded_read_preserves_original_model_answer(self):
        for suffix in ("", "[999]"):
            with self.subTest(suffix=suffix):
                result, answer, _ = asyncio.run(
                    _run_recovery(search_success=True, answer_suffix=suffix, degrade_read=True)
                )
                self.assertEqual(
                    [call["args"][0][0]["name"] for call in result.tool_execute_calls],
                    ["weather_forecast", "web_search", "url_read"],
                )
                visible = "".join(call["content"] for call in result.append_calls if call["chunk_type"] == "answering")
                self.assertEqual(visible, answer)
                self.assertEqual(result.session_status_calls[-1]["status"], "completed")
                self.assertEqual(result.persist_calls[-1]["block_types"][-1], "text")
                self.assertFalse(result.persist_calls[-1]["partial"])

    def test_weather_failure_uses_real_search_and_finishes_truthfully(self):
        for search_success in [True, False]:
            with self.subTest(search_success=search_success):
                result, answer, remote = asyncio.run(_run_recovery(search_success=search_success))
                assert [name for name, _, _ in remote.calls] == ["maps_geo", "maps_weather"]
                assert [call["args"][0][0]["name"] for call in result.tool_execute_calls] == [
                    "weather_forecast",
                    "web_search",
                ]
                first_tools = {tool["function"]["name"] for tool in result.llm_calls[0]["call_kwargs"]["tools"]}
                assert {"weather_forecast", "web_search", "url_read"} <= first_tools
                observation = "\n".join(
                    message["content"] for message in result.llm_calls[1]["messages"] if message["role"] == "tool"
                )
                assert "UNKNOWN_ERROR" in observation
                assert "alternative" in observation.lower()
                recovery_context = "\n".join(str(message["content"]) for message in result.llm_calls[2]["messages"])
                assert "web_search" in recovery_context
                assert "Available untried tools" in recovery_context
                visible = "".join(call["content"] for call in result.append_calls if call["chunk_type"] == "answering")
                assert "天气工具失败，我无法查询。" not in visible
                if search_success:
                    assert answer in visible
                    assert result.session_status_calls[-1]["status"] == "completed"
                else:
                    assert "本次查询仍未完成" in visible
                    assert result.session_status_calls[-1]["status"] == "incomplete"
                sequences = [event["sequence"] for event in result.events]
                assert sequences == list(range(len(sequences)))
                tool_events = [event for event in result.events if event["type"].startswith("tool_call_")]
                assert [event["tool_name"] for event in tool_events] == [
                    "weather_forecast",
                    "weather_forecast",
                    "web_search",
                    "web_search",
                ]
