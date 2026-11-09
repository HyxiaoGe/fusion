import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.core.config import settings  # noqa: E402
from app.core.logger import log_context  # noqa: E402
from app.services.external import agent_tools_mcp  # noqa: E402
from app.services.external.agent_tools_mcp import (  # noqa: E402
    GatewayToolCatalog,
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
    gateway_mcp_manager,
    parse_gateway_tool_payload,
    refresh_gateway_tool_catalog,
)
from app.services.mcp.client import McpClientManager  # noqa: E402
from test.test_mcp_client_manager import FakeConnector, FakeSession  # noqa: E402

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the weather forecast.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "The place.\x07"},
            "requested_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "description": "One specific day.",
            },
            "location_source": {
                "type": "string",
                "enum": ["named", "current_location"],
                "default": "named",
                "description": "named or current_location.",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    },
}


def payload(structured, text="model text"):
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}


class GatewayDefinitionTests(unittest.TestCase):
    def test_service_description_comes_first_and_parameter_descriptions_are_kept(self):
        spec = GatewayToolSpec(service="weather", **WEATHER_TOOL)

        definition = build_gateway_tool_definition(spec, name="weather_forecast", product_notes="In this application.")

        function = definition["function"]
        self.assertEqual(function["name"], "weather_forecast")
        self.assertEqual(function["description"], "Get the weather forecast.\n\nIn this application.")
        self.assertEqual(
            function["parameters"],
            {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The place."},
                    # 可选参数去掉 null 分支，说明接回剩下的一支；default 不进模型可见 schema。
                    "requested_date": {"type": "string", "description": "One specific day."},
                    "location_source": {
                        "type": "string",
                        "enum": ["named", "current_location"],
                        "description": "named or current_location.",
                    },
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        )


class GatewayPayloadTests(unittest.TestCase):
    def test_success_keeps_structured_data_and_model_text(self):
        outcome = parse_gateway_tool_payload(payload({"ok": True, "resolved_location": "杭州"}))

        self.assertEqual(
            outcome,
            GatewayToolOutcome(ok=True, data={"ok": True, "resolved_location": "杭州"}, model_text="model text"),
        )

    def test_service_failure_is_read_from_structured_content(self):
        error = {"code": "ambiguous_location", "message": "m", "retryable": False, "candidates": ["a", "b"]}

        outcome = parse_gateway_tool_payload(payload({"ok": False, "error": error}, text="ask the user"))

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_code, "ambiguous_location")
        self.assertEqual(outcome.error, error)
        self.assertEqual(outcome.model_text, "ask the user")

    def test_missing_structured_content_or_error_code_is_reported(self):
        self.assertEqual(
            parse_gateway_tool_payload({"content": [{"type": "text", "text": "x"}]}).error_code, "invalid_response"
        )
        self.assertEqual(parse_gateway_tool_payload(payload({"ok": False})).error_code, "tool_error")


class GatewayCallTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, session):
        connector = FakeConnector(session)
        manager = McpClientManager(
            policy=gateway_mcp_manager().policy,
            connector=connector,
            environ={"AGENT_TOOLS_API_KEY": "fusion-key"},
        )
        return manager, connector

    async def test_call_goes_to_the_gateway_with_the_product_key_attribution_and_meta(self):
        session = FakeSession(
            call_result=SimpleNamespace(isError=False, model_dump=lambda **_: payload({"ok": True, "day_count": 4}))
        )
        manager, connector = self.manager(session)
        meta = {"agent-tools/geolocation": {"longitude": 120.16, "latitude": 30.27}}

        with log_context(user_id="user-1", run_id="run-1"):
            outcome = await call_gateway_tool(
                "weather",
                "get_weather",
                {"location": "杭州"},
                scene="chat_weather",
                meta=meta,
                manager=manager,
            )

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.data, {"ok": True, "day_count": 4})
        connection = connector.connections[0]
        self.assertEqual(connection["endpoint_url"], "http://agent-tools-gateway:8100/weather/mcp")
        self.assertEqual(
            connection["headers"],
            {
                "Authorization": "Bearer fusion-key",
                "X-Caller-Scene": "chat_weather",
                "X-Caller-User": "user-1",
                "X-Request-ID": "run-1",
            },
        )
        self.assertEqual(session.calls, [("get_weather", {"location": "杭州"})])
        self.assertEqual(session.call_metas, [meta])

    async def test_gateway_daily_limit_is_a_failure_without_service_text(self):
        request = httpx.Request("POST", "http://agent-tools-gateway:8100/weather/mcp")

        class LimitedSession(FakeSession):
            async def call_tool(self, name, arguments, read_timeout_seconds=None, *, meta=None):
                raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

        manager, _ = self.manager(LimitedSession())

        outcome = await call_gateway_tool(
            "weather", "get_weather", {"location": "杭州"}, scene="chat_weather", manager=manager
        )

        self.assertEqual(outcome, GatewayToolOutcome(ok=False, error_code="rate_limited"))

    async def test_only_migrated_gateway_services_are_reachable(self):
        manager, connector = self.manager(FakeSession())

        outcome = await call_gateway_tool("amap", "maps_geo", {}, scene="chat", manager=manager)

        self.assertEqual(outcome.error_code, "invalid_endpoint")
        self.assertEqual(connector.connections, [])


class FakeLister:
    def __init__(self, tools=None):
        self.tools = tools if tools is not None else [WEATHER_TOOL]
        self.calls = 0
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def __call__(self, service):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.tools


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


class GatewayCatalogTests(unittest.IsolatedAsyncioTestCase):
    def catalog(self, lister, clock, **kwargs):
        return GatewayToolCatalog(
            services=("weather",),
            list_tools=lister,
            clock=clock,
            ttl_seconds=600,
            retry_after_failure_seconds=30,
            **kwargs,
        )

    async def test_first_refresh_waits_for_the_snapshot_and_later_ones_reuse_it(self):
        lister, clock = FakeLister(), FakeClock()
        catalog = self.catalog(lister, clock)

        self.assertIsNone(catalog.spec("weather", "get_weather"))
        await catalog.refresh()
        await catalog.refresh()

        spec = catalog.spec("weather", "get_weather")
        self.assertEqual(spec.description, "Get the weather forecast.")
        self.assertEqual(spec.input_schema, WEATHER_TOOL["input_schema"])
        self.assertIsNone(catalog.spec("weather", "unknown"))
        self.assertEqual(lister.calls, 1)

    async def test_stale_snapshot_is_served_while_refreshing_in_the_background(self):
        lister, clock = FakeLister(), FakeClock()
        catalog = self.catalog(lister, clock)
        await catalog.refresh()
        lister.tools = [{**WEATHER_TOOL, "description": "New description."}]
        lister.gate = asyncio.Event()
        clock.now += 601

        # 有旧快照时不等网络：目录还没刷新完，refresh 已经返回、读到的仍是旧说明。
        await asyncio.wait_for(catalog.refresh(), timeout=1)
        self.assertEqual(catalog.spec("weather", "get_weather").description, "Get the weather forecast.")

        lister.gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(catalog.spec("weather", "get_weather").description, "New description.")
        self.assertEqual(lister.calls, 2)

    async def test_failed_refresh_keeps_the_old_snapshot_and_backs_off(self):
        lister, clock = FakeLister(), FakeClock()
        catalog = self.catalog(lister, clock)
        await catalog.refresh()
        lister.error = RuntimeError("gateway down")
        clock.now += 601

        with self.assertLogs("app", level="WARNING") as logs:
            await catalog.refresh()
            await asyncio.sleep(0)
        clock.now += 10
        await catalog.refresh()

        self.assertIn("service=weather error_code=RuntimeError", "\n".join(logs.output))
        self.assertEqual(catalog.spec("weather", "get_weather").description, "Get the weather forecast.")
        self.assertEqual(lister.calls, 2)
        clock.now += 30
        await catalog.refresh()
        await asyncio.sleep(0)
        self.assertEqual(lister.calls, 3)

    async def test_without_a_snapshot_the_wait_is_bounded_and_the_load_finishes_later(self):
        lister, clock = FakeLister(), FakeClock()
        lister.gate = asyncio.Event()
        catalog = self.catalog(lister, clock, first_load_wait_seconds=0.01)

        await catalog.refresh()
        self.assertIsNone(catalog.spec("weather", "get_weather"))
        # 还在加载时再来一个 Run 不会重复请求。
        await catalog.refresh()
        self.assertEqual(lister.calls, 1)

        lister.gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIsNotNone(catalog.spec("weather", "get_weather"))

    async def test_refresh_does_nothing_without_the_product_key(self):
        refresh = AsyncMock()
        with (
            patch.object(settings, "AGENT_TOOLS_API_KEY", " "),
            patch.object(agent_tools_mcp._CATALOG, "refresh", refresh),
        ):
            await refresh_gateway_tool_catalog()
        refresh.assert_not_awaited()

        refresh.side_effect = RuntimeError("boom")
        with (
            patch.object(settings, "AGENT_TOOLS_API_KEY", "fusion-key"),
            patch.object(agent_tools_mcp._CATALOG, "refresh", refresh),
            self.assertLogs("app", level="WARNING"),
        ):
            await refresh_gateway_tool_catalog()
        refresh.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
