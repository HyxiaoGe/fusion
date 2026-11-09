import asyncio
import json
import os
import unittest
from datetime import date, datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt  # noqa: E402
from app.services.agent.context_broker import Geolocation  # noqa: E402
from app.services.external.agent_tools_mcp import (  # noqa: E402
    GatewayToolCatalog,
    GatewayToolOutcome,
    GatewayToolSpec,
)
from app.services.mcp.agent_tools import load_mcp_agent_tools  # noqa: E402
from app.services.tool_handlers.base import ToolRuntimeContext  # noqa: E402
from app.services.weather.forecast_tool import (  # noqa: E402
    WeatherForecastToolHandler,
    build_weather_binding,
    build_weather_forecast_definition,
)
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository  # noqa: E402

# 与 dev 上 get_weather 的 tools/list 同形（说明取开头一句）。
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the weather forecast (QWeather) for a named place.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "The complete place text the user supplied."},
            "requested_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "description": "One specific day the user asks about, in YYYY-MM-DD form.",
            },
            "location_source": {
                "type": "string",
                "enum": ["named", "current_location"],
                "default": "named",
                "description": "named for a place name in location.",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    },
}
WEATHER_SPEC = GatewayToolSpec(service="weather", **WEATHER_TOOL)
CITY_LEVEL = "天气预报按城市或区县提供，不代表具体建筑物"


def forecast_day(day: date) -> dict:
    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "day_weather": "晴",
        "night_weather": "多云",
        "high_c": 26,
        "low_c": 17,
        "day_wind_direction": "东风",
        "day_wind_power": "1-3级",
        "night_wind_direction": "北风",
        "night_wind_power": "1-3级",
    }


def hourly_points(count=24) -> list[dict]:
    start = datetime.fromisoformat("2026-10-08T16:00:00+08:00")
    return [
        {
            "time": (start + timedelta(hours=hour)).isoformat(),
            "temp_c": 20 - hour // 4,
            "weather": "小雨" if 3 <= hour <= 5 else "多云",
            "pop": 70 if 3 <= hour <= 5 else 10,
            "precip_mm": 0.6 if 3 <= hour <= 5 else 0.0,
        }
        for hour in range(count)
    ]


def service_result(*, days=4, hourly=None, limitations=None, **extra) -> dict:
    first = date(2026, 10, 8)
    return {
        "ok": True,
        "resolved_location": "浙江省·杭州",
        "local_today": first.isoformat(),
        "day_count": days,
        "forecast_days": [forecast_day(first + timedelta(days=offset)) for offset in range(days)],
        "hourly": hourly_points() if hourly is None else hourly,
        "fetched_at": "2026-10-08T16:00:00+08:00",
        "limitations": limitations or [],
        **extra,
    }


def success(result: dict) -> GatewayToolOutcome:
    model_payload = {key: value for key, value in result.items() if key != "hourly"}
    return GatewayToolOutcome(ok=True, data=result, model_text=json.dumps(model_payload, ensure_ascii=False))


class FakeWeatherGateway:
    """代替 call_gateway_tool：记录每次调用，按给定结果答复。"""

    def __init__(self, outcome: GatewayToolOutcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    async def __call__(self, service, tool_name, arguments, **kwargs):
        self.calls.append({"service": service, "tool": tool_name, "arguments": arguments, **kwargs})
        return self.outcome


def build_handler(gateway, **kwargs) -> WeatherForecastToolHandler:
    definition = build_weather_forecast_definition(WEATHER_SPEC)
    return WeatherForecastToolHandler(
        binding=build_weather_binding(definition),
        parameters=definition["function"]["parameters"],
        call_tool=gateway,
        **kwargs,
    )


def run(handler, args, context=None):
    return asyncio.run(handler.execute(args, context))


class WeatherDefinitionTests(unittest.TestCase):
    def test_service_owns_the_description_and_parameters_and_fusion_appends_its_notes(self):
        definition = build_weather_forecast_definition(WEATHER_SPEC)["function"]

        self.assertEqual(definition["name"], "weather_forecast")
        self.assertEqual(
            definition["description"],
            f"{WEATHER_TOOL['description']}\n\n{render_runtime_prompt('qweather.product_notes')}",
        )
        properties = definition["parameters"]["properties"]
        self.assertEqual(set(properties), {"location", "requested_date", "location_source"})
        self.assertEqual(properties["requested_date"]["type"], "string")
        self.assertEqual(properties["location_source"]["enum"], ["named", "current_location"])
        self.assertEqual(definition["parameters"]["required"], ["location"])

    def test_arguments_are_checked_against_the_announced_schema_only(self):
        handler = build_handler(FakeWeatherGateway(success(service_result())))

        self.assertEqual(handler.validate_arguments({"location": "杭州"}), [])
        self.assertEqual(
            handler.validate_arguments({"location": "当前位置", "location_source": "current_location"}), []
        )
        self.assertTrue(handler.validate_arguments({"location": "杭州", "location_source": "gps"}))
        self.assertTrue(handler.validate_arguments({"location": "杭州", "lng": 120}))
        self.assertTrue(handler.validate_arguments({}))
        self.assertEqual(handler.validate_arguments("杭州"), [{"field": "request", "code": "invalid_arguments"}])

    def test_same_arguments_share_a_successful_call_signature(self):
        handler = build_handler(FakeWeatherGateway(success(service_result())))

        first = handler.build_successful_call_signature({"location": "杭州", "requested_date": "2026-10-09"})
        same = handler.build_successful_call_signature({"requested_date": "2026-10-09", "location": "杭州"})
        other = handler.build_successful_call_signature({"location": "杭州"})

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)


class WeatherExecutionTests(unittest.TestCase):
    def test_named_place_is_forwarded_unchanged_and_drawn_as_a_card(self):
        gateway = FakeWeatherGateway(success(service_result()))
        handler = build_handler(gateway)

        result = run(handler, {"location": "杭州", "location_source": "named"})

        self.assertEqual(
            gateway.calls,
            [
                {
                    "service": "weather",
                    "tool": "get_weather",
                    "arguments": {"location": "杭州", "location_source": "named"},
                    "scene": "chat_weather",
                    "meta": None,
                    "min_call_timeout_seconds": 25.0,
                }
            ],
        )
        self.assertEqual(result.status, "success")
        block = handler.build_content_block(result, "blk-1", "log-1")
        self.assertEqual(block.provider, "qweather")
        self.assertEqual(block.query, "杭州")
        self.assertEqual(block.resolved_location, "浙江省·杭州")
        self.assertEqual(block.day_count, 4)
        self.assertEqual([day.weekday for day in block.forecast_days], [4, 5, 6, 7])
        self.assertEqual(block.forecast_days[0].day_wind_power, "1-3级")
        self.assertEqual(len(block.hourly), 24)
        self.assertEqual(block.limitations, [CITY_LEVEL])
        self.assertEqual(block.fetched_at.utcoffset(), timedelta(hours=8))
        self.assertEqual(block.tool_call_log_id, "log-1")

    def test_model_reads_the_service_text_inside_the_untrusted_result_wrapper(self):
        outcome = success(service_result())
        handler = build_handler(FakeWeatherGateway(outcome))

        context = handler.format_llm_context(run(handler, {"location": "杭州"}))

        self.assertIn(outcome.model_text, context)
        self.assertIn('<amap_product_result tool="weather_forecast">', context)
        self.assertNotIn('"hourly":', context)
        self.assertNotIn("Hard result-usage constraints", context)

    def test_current_location_sends_the_device_location_as_meta(self):
        gateway = FakeWeatherGateway(success(service_result()))
        handler = build_handler(gateway)
        geolocation = Geolocation(latitude=30.274, longitude=120.155, accuracy_m=30, acquired_at=1.0)

        result = run(
            handler,
            {"location": "current_location", "location_source": "current_location"},
            ToolRuntimeContext(geolocation=geolocation),
        )

        self.assertEqual(
            gateway.calls[0]["meta"],
            {"agent-tools/geolocation": {"longitude": 120.155, "latitude": 30.274, "accuracy_m": 30.0}},
        )
        self.assertEqual(result.data["query"], "当前位置")
        self.assertEqual(handler.build_content_block(result, "blk-1", "log-1").query, "当前位置")

    def test_current_location_without_a_device_location_lets_the_service_answer(self):
        unavailable = GatewayToolOutcome(
            ok=False,
            model_text="No forecast was fetched: the device's current location is not available.",
            error_code="location_unavailable",
            error={"code": "location_unavailable", "message": "m", "retryable": False},
        )
        gateway = FakeWeatherGateway(unavailable)
        handler = build_handler(gateway)

        result = run(handler, {"location": "current_location", "location_source": "current_location"})

        self.assertIsNone(gateway.calls[0]["meta"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(handler.format_llm_context(result), unavailable.model_text)

    def test_named_place_never_sends_the_device_location(self):
        gateway = FakeWeatherGateway(success(service_result()))
        geolocation = Geolocation(latitude=30.274, longitude=120.155, accuracy_m=30, acquired_at=1.0)

        run(build_handler(gateway), {"location": "杭州"}, ToolRuntimeContext(geolocation=geolocation))

        self.assertIsNone(gateway.calls[0]["meta"])

    def test_service_failure_text_goes_to_the_model_and_no_card_is_drawn(self):
        ambiguous = GatewayToolOutcome(
            ok=False,
            model_text='No forecast was fetched: the place name matches several areas. Candidates: ["北京·朝阳", "辽宁·朝阳"].',
            error_code="ambiguous_location",
            error={"code": "ambiguous_location", "retryable": False, "candidates": ["北京·朝阳", "辽宁·朝阳"]},
        )
        handler = build_handler(FakeWeatherGateway(ambiguous))

        result = run(handler, {"location": "朝阳"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "ambiguous_location")
        self.assertFalse(result.data["retryable"])
        self.assertIsNone(handler.build_content_block(result, "blk-1", "log-1"))
        self.assertEqual(handler.format_llm_context(result), ambiguous.model_text)
        log = handler.sanitize_output_data_for_log(result)
        self.assertEqual(log["error_code"], "ambiguous_location")
        self.assertEqual(log["candidate_count"], 2)

    def test_gateway_failure_gets_fusion_text(self):
        handler = build_handler(FakeWeatherGateway(GatewayToolOutcome(ok=False, error_code="rate_limited")))

        with self.assertLogs("app", level="WARNING") as logs:
            result = run(handler, {"location": "杭州"})

        self.assertEqual(result.data["error_code"], "rate_limited")
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("qweather.weather_unavailable"))
        self.assertIn("error_code=rate_limited", "\n".join(logs.output))

    def test_success_without_model_text_is_an_invalid_response(self):
        handler = build_handler(FakeWeatherGateway(GatewayToolOutcome(ok=True, data=service_result())))

        with self.assertLogs("app", level="WARNING"):
            result = run(handler, {"location": "杭州"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "invalid_response")
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("qweather.weather_unavailable"))

    def test_uncovered_date_is_degraded_without_a_card(self):
        uncovered = service_result(
            days=0,
            hourly=[],
            limitations=["requested_date_not_covered"],
            requested_date="2026-12-01",
            forecast_range={"first_date": "2026-10-08", "last_date": "2026-10-22"},
        )
        outcome = success(uncovered)
        handler = build_handler(FakeWeatherGateway(outcome))

        result = run(handler, {"location": "杭州", "requested_date": "2026-12-01"})

        self.assertEqual(result.status, "degraded")
        self.assertIsNone(handler.build_content_block(result, "blk-1", "log-1"))
        self.assertIn("forecast_range", handler.format_llm_context(result))

    def test_card_limitations_are_shown_in_chinese(self):
        handler = build_handler(
            FakeWeatherGateway(
                success(service_result(days=2, hourly=[], limitations=["fewer_days", "hourly_unavailable"]))
            )
        )

        result = run(handler, {"location": "杭州"})

        self.assertEqual(result.status, "degraded")
        block = handler.build_content_block(result, "blk-1", "log-1")
        self.assertEqual(block.limitations, [CITY_LEVEL, "仅返回 2 天有效预报", "逐小时预报暂时取不到"])
        self.assertEqual(block.hourly, [])

    def test_hourly_problems_only_drop_the_affected_points(self):
        hourly = hourly_points(26)
        hourly[1]["weather"] = ""
        hourly[2] = dict(hourly[0])
        handler = build_handler(FakeWeatherGateway(success(service_result(hourly=hourly))))

        block = handler.build_content_block(run(handler, {"location": "杭州"}), "blk-1", "log-1")

        self.assertEqual(len(block.hourly), 24)
        self.assertEqual(block.hourly[1].time.hour, 19)


def loaded_catalog(tools: list[dict]) -> GatewayToolCatalog:
    async def list_tools(_service):
        return tools

    catalog = GatewayToolCatalog(services=("weather",), list_tools=list_tools)
    asyncio.run(catalog.refresh())
    return catalog


class WeatherRegistrationTests(unittest.TestCase):
    def load(self, catalog):
        repository = FakeRepository([])
        return load_mcp_agent_tools(
            object(),
            client_manager=FakeClientManager(),
            session_factory=FakeDb,
            repository_factory=lambda _db: repository,
            gateway_catalog=catalog,
            maps_hidden=lambda: True,
        )

    def test_weather_tool_comes_from_the_service_catalog(self):
        tools = self.load(loaded_catalog([WEATHER_TOOL]))

        self.assertIsInstance(tools.handlers["weather_forecast"], WeatherForecastToolHandler)
        names = [definition["function"]["name"] for definition in tools.definitions]
        self.assertEqual(names.count("weather_forecast"), 1)
        weather = next(d for d in tools.definitions if d["function"]["name"] == "weather_forecast")
        self.assertEqual(weather, build_weather_forecast_definition(WEATHER_SPEC))
        binding = next(b for b in tools.audit_bindings if b["alias"] == "weather_forecast")
        self.assertEqual(binding["provider"], "qweather")
        self.assertEqual(binding["remote_tool_name"], "get_weather")

    def test_without_the_service_in_the_catalog_no_weather_tool_is_registered(self):
        for catalog in (GatewayToolCatalog(services=("weather",)), loaded_catalog([])):
            with self.subTest(catalog=catalog):
                tools = self.load(catalog)
                self.assertNotIn("weather_forecast", tools.handlers)


if __name__ == "__main__":
    unittest.main()
