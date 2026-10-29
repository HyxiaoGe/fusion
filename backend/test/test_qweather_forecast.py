import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.core.config import settings  # noqa: E402
from app.services.mcp.agent_tools import _build_qweather_client, load_mcp_agent_tools  # noqa: E402
from app.services.weather.qweather_client import (  # noqa: E402
    QWeatherCity,
    QWeatherClient,
    QWeatherError,
)
from app.services.weather.qweather_forecast_tool import (  # noqa: E402
    WEATHER_FORECAST_DEFINITION,
    QWeatherForecastToolHandler,
    _ForecastCache,
    _Unresolved,
    build_qweather_binding,
    city_label,
    select_city,
)
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository, build_row  # noqa: E402

SHANGHAI = ZoneInfo("Asia/Shanghai")
GATEWAY_WEATHER = "http://agent-tools-gateway:8100/weather"


def city(name, adm2, adm1, country="中国", location_id=None, rank=15):
    return QWeatherCity(
        location_id=location_id or f"id-{adm1}-{adm2}-{name}",
        name=name,
        adm1=adm1,
        adm2=adm2,
        country=country,
        rank=rank,
    )


# agent-tools weather 代理返回的已归一字段。
DAILY = [
    {
        "date": f"2026-10-{day:02d}",
        "text_day": "晴",
        "text_night": "多云",
        "temp_max": "26",
        "temp_min": "17",
        "wind_dir_day": "东风",
        "wind_dir_night": "北风",
        "wind_scale_day": "1-3",
        "wind_scale_night": "1-3",
    }
    for day in range(8, 15)
]


HOURLY = [
    {
        "time": f"2026-10-{8 + (16 + hour) // 24:02d}T{(16 + hour) % 24:02d}:00+08:00",
        "temp": str(20 - hour // 4),
        "text": "小雨" if 3 <= hour <= 5 else "多云",
        "pop": str(70 if 3 <= hour <= 5 else 10),
        "precip": "0.6" if 3 <= hour <= 5 else "0.0",
    }
    for hour in range(24)
]


class FakeQWeather:
    """模拟网关后的 weather 代理：按路径回归一后的数据，可指定整体或逐小时接口的失败。"""

    def __init__(self, *, cities=None, daily=None, status=200, error_code=None, hourly_status=200):
        self.cities = cities if cities is not None else [city("杭州", "杭州", "浙江省", location_id="101210101")]
        self.daily = daily if daily is not None else DAILY
        self.status = status
        self.error_code = error_code
        self.hourly_status = hourly_status
        self.requests = []

    def transport(self):
        def handle(request):
            self.requests.append(request)
            if self.status != 200:
                body = {"error": {"code": self.error_code}} if self.error_code else {"error": "unauthorized"}
                return httpx.Response(self.status, json=body)
            if request.url.path == "/weather/v1/cities":
                return httpx.Response(
                    200,
                    json={
                        "cities": [
                            {
                                "location_id": c.location_id,
                                "name": c.name,
                                "adm1": c.adm1,
                                "adm2": c.adm2,
                                "country": c.country,
                                "rank": c.rank,
                            }
                            for c in self.cities
                        ]
                    },
                )
            if request.url.path == "/weather/v1/forecast/hourly":
                if self.hourly_status != 200:
                    return httpx.Response(self.hourly_status, json={"error": {"code": "upstream_unavailable"}})
                return httpx.Response(200, json={"hourly": HOURLY})
            return httpx.Response(200, json={"daily": self.daily})

        return httpx.MockTransport(handle)


def build_handler(fake, **kwargs):
    client = QWeatherClient(transport=fake.transport())
    return QWeatherForecastToolHandler(
        binding=build_qweather_binding(),
        client=client,
        cache=_ForecastCache(),
        now=lambda: datetime(2026, 10, 8, 16, 0, tzinfo=SHANGHAI),
        **kwargs,
    )


class QWeatherClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_requests_go_to_the_gateway_with_the_product_key(self):
        fake = FakeQWeather()
        client = QWeatherClient(transport=fake.transport())
        with patch.object(settings, "AGENT_TOOLS_API_KEY", "fusion-service-key"):
            cities = await client.lookup_city("杭州", number=3, lang="en")
            await client.daily_forecast("101210101")
            await client.hourly_forecast("101210101")

        self.assertEqual([c.location_id for c in cities], ["101210101"])
        self.assertEqual(
            [str(r.url).split("?")[0] for r in fake.requests],
            [
                f"{GATEWAY_WEATHER}/v1/cities",
                f"{GATEWAY_WEATHER}/v1/forecast/daily",
                f"{GATEWAY_WEATHER}/v1/forecast/hourly",
            ],
        )
        self.assertEqual(dict(fake.requests[0].url.params), {"location": "杭州", "number": "3", "lang": "en"})
        self.assertEqual(dict(fake.requests[1].url.params), {"location_id": "101210101"})
        for request in fake.requests:
            self.assertEqual(request.headers["authorization"], "Bearer fusion-service-key")
            self.assertEqual(request.headers["x-caller-scene"], "weather_forecast")

    async def test_proxy_errors_map_to_codes(self):
        cases = (
            # 代理的结构化错误码原样保留。
            (503, "weather_not_configured", "weather_not_configured"),
            (502, "upstream_auth_failed", "upstream_auth_failed"),
            (429, "upstream_rate_limited", "upstream_rate_limited"),
            (504, "upstream_timeout", "upstream_timeout"),
            # 未知错误码按状态归类：网关拒绝（产品密钥 / 服务未放行）视为鉴权失败，其余视为不可用。
            (401, None, "upstream_auth_failed"),
            (403, None, "upstream_auth_failed"),
            (500, None, "upstream_unavailable"),
            (502, "something_new", "upstream_unavailable"),
        )
        for status, body_code, expected in cases:
            with self.subTest(status=status, body_code=body_code):
                client = QWeatherClient(transport=FakeQWeather(status=status, error_code=body_code).transport())
                with self.assertRaises(QWeatherError) as raised:
                    await client.daily_forecast("101210101")
                self.assertEqual(raised.exception.code, expected)

    async def test_transport_failures_and_malformed_bodies_are_reported(self):
        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        client = QWeatherClient(transport=httpx.MockTransport(timeout))
        with self.assertRaises(QWeatherError) as raised:
            await client.lookup_city("杭州")
        self.assertEqual(raised.exception.code, "upstream_timeout")

        client = QWeatherClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"[]")))
        with self.assertRaises(QWeatherError) as raised:
            await client.lookup_city("杭州")
        self.assertEqual(raised.exception.code, "invalid_response")

        # 字段不全或类型不对的条目丢弃，不让坏数据进入产品结果。
        broken = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"cities": [{"location_id": "1", "name": "杭州", "rank": "15"}, {"name": "缺 id"}, "x"]},
            )
        )
        cities = await QWeatherClient(transport=broken).lookup_city("杭州")
        self.assertEqual([(c.location_id, c.rank, c.adm1) for c in cities], [("1", None, "")])


class SelectCityTests(unittest.TestCase):
    def test_resolution_rules(self):
        hangzhou = city("杭州", "杭州", "浙江省")
        xiaoshan = city("萧山", "杭州", "浙江省")
        chaoyang_ln = city("朝阳", "朝阳", "辽宁省")
        chaoyang_bj = city("朝阳", "北京", "北京市")
        beijing = city("北京", "北京", "北京市")
        haidian = city("海淀", "北京", "北京市")
        xihu_hz = city("西湖", "杭州", "浙江省")
        xihu_nc = city("西湖", "南昌", "江西省")
        france_wuzhen = city("乌镇", "厄尔-卢瓦省", "中央-卢瓦尔河谷大区", country="法国", rank=83)
        xinbei_tw = city("新北市", "新北市", "台湾省", rank=85)
        xinbei_cz = city("新北", "常州", "江苏省", rank=35)

        self.assertIs(select_city("杭州", [hangzhou, xiaoshan]), hangzhou)
        self.assertIs(select_city("杭州市", [hangzhou, xiaoshan]), hangzhou)
        self.assertIs(select_city("北京朝阳", [chaoyang_ln, chaoyang_bj, beijing]), chaoyang_bj)
        self.assertIs(select_city("北京市海淀区中关村", [haidian, beijing, chaoyang_bj]), haidian)
        self.assertIs(select_city("杭州西湖", [xihu_hz, xihu_nc]), xihu_hz)
        self.assertIs(select_city("新北市", [xinbei_cz, xinbei_tw]), xinbei_tw)
        self.assertIs(select_city(city_label(xihu_nc), [xihu_hz, xihu_nc]), xihu_nc)

        with self.assertRaises(_Unresolved) as ambiguous:
            select_city("朝阳", [chaoyang_ln, chaoyang_bj])
        self.assertEqual(ambiguous.exception.code, "ambiguous_location")
        self.assertEqual(ambiguous.exception.candidates, ["辽宁省·朝阳", "北京市·北京·朝阳"])

        for query, cities in (
            ("乌镇", [france_wuzhen]),
            ("不存在的地方xyz", [city("Shahumyan", "x", "y", "亚美尼亚", rank=85)]),
        ):
            with self.subTest(query=query):
                with self.assertRaises(_Unresolved) as missing:
                    select_city(query, cities)
                self.assertEqual(missing.exception.code, "location_not_found")

    def test_labels_include_country_only_abroad(self):
        self.assertEqual(city_label(city("杭州", "杭州", "浙江省")), "浙江省·杭州")
        self.assertEqual(city_label(city("东京", "东京", "东京都", country="日本")), "日本·东京都·东京")


class WeatherContractTests(unittest.TestCase):
    def test_definition_is_a_closed_contract_that_allows_hourly_summary(self):
        function = WEATHER_FORECAST_DEFINITION["function"]
        schema = function["parameters"]
        self.assertEqual(function["name"], "weather_forecast")
        self.assertEqual(set(schema["required"]), {"location", "location_source"})
        self.assertEqual(set(schema["properties"]), {"location", "location_source", "requested_date"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["requested_date"]["format"], "date")
        description = function["description"]
        self.assertIn("result.hourly_summary", description)
        self.assertIn("do not add a city the user did not provide", description)
        self.assertIn("not for whole provinces", description)
        self.assertIn("query its capital or main city instead", description)
        self.assertIn("Omit requested_date for negated", description)
        self.assertNotIn("precipitation probability, or alerts", description)

    def test_arguments_are_validated_before_any_request(self):
        handler = build_handler(FakeQWeather())
        named = {"location": "深圳市", "location_source": "named"}
        self.assertEqual(handler.validate_arguments({**named, "requested_date": "2026-10-20"}), [])
        invalid = [{"field": "request", "code": "invalid_arguments"}]
        for args in (
            {**named, "requested_date": "2026-02-30"},
            {**named, "city": "深圳市"},
            {"location": "114.05,22.54", "location_source": "named"},
            {"location": "440300", "location_source": "named"},
            {"location": "深圳市", "location_source": "current_location"},
        ):
            with self.subTest(args=args):
                self.assertEqual(handler.validate_arguments(args), invalid)
        self.assertEqual(
            {error["field"] for error in handler.validate_arguments({"location": "深圳南山区"})},
            {"location_source"},
        )
        first = handler.build_successful_call_signature({"location": "上海市", "location_source": "named"})
        second = handler.build_successful_call_signature({"location_source": "named", "location": "上海市"})
        self.assertIsNotNone(first)
        self.assertEqual(first, second)


class QWeatherHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_location_returns_four_day_block(self):
        fake = FakeQWeather()
        handler = build_handler(fake)

        result = await handler.execute({"location": "杭州", "location_source": "named"})

        self.assertEqual(result.status, "success")
        block = handler.build_content_block(result, "blk", "log")
        self.assertEqual(block.provider, "qweather")
        self.assertEqual(block.attribution.label, "和风天气")
        self.assertEqual(block.resolved_location, "浙江省·杭州")
        self.assertEqual(block.day_count, 4)
        self.assertEqual(block.forecast_days[0].day_wind_power, "1-3级")
        self.assertEqual(block.forecast_days[0].weekday, 4)
        self.assertIsNone(block.requested_date)
        self.assertIn("forecast_days", handler.format_llm_context(result))
        self.assertEqual(fake.requests[-1].url.params["location_id"], "101210101")

    async def test_requested_date_in_window_returns_only_that_day(self):
        handler = build_handler(FakeQWeather())

        result = await handler.execute({"location": "杭州", "location_source": "named", "requested_date": "2026-10-10"})

        self.assertEqual(result.status, "success")
        block = handler.build_content_block(result, "blk", "log")
        self.assertEqual(block.day_count, 1)
        self.assertEqual([str(day.date) for day in block.forecast_days], ["2026-10-10"])
        self.assertEqual(str(block.requested_date), "2026-10-10")
        context = handler.format_llm_context(result)
        self.assertIn("2026-10-10", context)
        self.assertNotIn("2026-10-11", context)

    async def test_requested_date_outside_window_keeps_whole_window(self):
        handler = build_handler(FakeQWeather())

        result = await handler.execute({"location": "杭州", "location_source": "named", "requested_date": "2026-10-20"})

        self.assertEqual(result.status, "success")
        block = handler.build_content_block(result, "blk", "log")
        self.assertEqual(block.day_count, 4)
        self.assertEqual(str(block.requested_date), "2026-10-20")

    async def test_hourly_goes_to_card_and_only_summary_to_model(self):
        fake = FakeQWeather()
        handler = build_handler(fake)

        result = await handler.execute({"location": "杭州", "location_source": "named"})

        block = handler.build_content_block(result, "blk", "log")
        self.assertEqual(len(block.hourly), 24)
        self.assertEqual(block.hourly[0].pop, 10)
        self.assertEqual(block.hourly[0].time.isoformat(), "2026-10-08T16:00:00+08:00")
        context = handler.format_llm_context(result)
        self.assertNotIn('"hourly"', context)
        self.assertIn('"hourly_summary"', context)
        self.assertIn('"max_pop_percent": 70', context)
        self.assertIn('"first_precipitation_time": "2026-10-08T19:00:00+08:00"', context)
        self.assertIn('"precipitation_hours": 3', context)
        self.assertIn("result.hourly_summary", context)

    async def test_hourly_only_for_today_tomorrow_or_unspecified(self):
        for requested_date, expected in (("2026-10-08", True), ("2026-10-09", True), ("2026-10-10", False)):
            with self.subTest(requested_date=requested_date):
                fake = FakeQWeather()
                result = await build_handler(fake).execute(
                    {"location": "杭州", "location_source": "named", "requested_date": requested_date}
                )
                self.assertEqual("hourly" in result.data["result"], expected)
                self.assertEqual(any(r.url.path == "/weather/v1/forecast/hourly" for r in fake.requests), expected)

    async def test_hourly_keeps_only_requested_day_hours(self):
        # 逐小时从 2026-10-08 16:00 起 24 点：今天 16–23 时 8 点，明天 0–15 时 16 点
        for requested_date, count, first, last in (
            ("2026-10-08", 8, "2026-10-08T16:00:00+08:00", "2026-10-08T23:00:00+08:00"),
            ("2026-10-09", 16, "2026-10-09T00:00:00+08:00", "2026-10-09T15:00:00+08:00"),
        ):
            with self.subTest(requested_date=requested_date):
                handler = build_handler(FakeQWeather())
                result = await handler.execute(
                    {"location": "杭州", "location_source": "named", "requested_date": requested_date}
                )
                block = handler.build_content_block(result, "blk", "log")
                self.assertEqual(len(block.hourly), count)
                self.assertEqual(block.hourly[0].time.isoformat(), first)
                self.assertEqual(block.hourly[-1].time.isoformat(), last)
                self.assertIn(f'"window": "{first} ~ {last}"', handler.format_llm_context(result))

    async def test_hourly_failure_keeps_daily_forecast(self):
        handler = build_handler(FakeQWeather(hourly_status=500))
        result = await handler.execute({"location": "杭州", "location_source": "named"})
        self.assertEqual(result.status, "success")
        self.assertEqual(handler.build_content_block(result, "blk", "log").hourly, [])

    async def test_forecast_is_cached_per_location(self):
        fake = FakeQWeather()
        handler = build_handler(fake)
        await handler.execute({"location": "杭州", "location_source": "named"})
        await handler.execute({"location": "杭州", "location_source": "named"})
        forecast_calls = [r for r in fake.requests if r.url.path == "/weather/v1/forecast/daily"]
        self.assertEqual(len(forecast_calls), 1)

    async def test_current_location_uses_coordinates(self):
        fake = FakeQWeather()
        handler = build_handler(fake)
        context = SimpleNamespace(geolocation=SimpleNamespace(latitude=30.2741, longitude=120.1551))

        result = await handler.execute({"location": "current_location", "location_source": "current_location"}, context)

        self.assertEqual(result.status, "success")
        self.assertEqual(fake.requests[0].url.params["location"], "120.16,30.27")

        missing = await handler.execute({"location": "current_location", "location_source": "current_location"})
        self.assertEqual(missing.data["error_code"], "location_context_unavailable")

    async def test_ambiguous_and_not_found_reach_model_without_block(self):
        fake = FakeQWeather(cities=[city("朝阳", "朝阳", "辽宁省"), city("朝阳", "北京", "北京市")])
        handler = build_handler(fake)

        result = await handler.execute({"location": "朝阳", "location_source": "named"})

        self.assertEqual(result.data["error_code"], "ambiguous_location")
        self.assertIsNone(handler.build_content_block(result, "blk", "log"))
        context = handler.format_llm_context(result)
        self.assertIn("北京市·北京·朝阳", context)
        self.assertEqual([r.url.path for r in fake.requests], ["/weather/v1/cities"])

        fake.cities = []
        not_found = await handler.execute({"location": "鼓浪屿", "location_source": "named"})
        self.assertEqual(not_found.data["error_code"], "location_not_found")

    async def test_ascii_query_looks_up_english_names(self):
        fake = FakeQWeather(cities=[city("Paris", "Paris", "Ile-de-France", country="France", rank=20)])
        handler = build_handler(fake)
        result = await handler.execute({"location": "Paris", "location_source": "named"})
        self.assertEqual(result.status, "success")
        self.assertEqual(fake.requests[0].url.params["lang"], "en")

    async def test_candidate_label_retry_looks_up_last_segment(self):
        fake = FakeQWeather(cities=[city("西湖", "杭州", "浙江省"), city("西湖", "南昌", "江西省", location_id="nc")])
        handler = build_handler(fake)
        result = await handler.execute({"location": "江西省·南昌·西湖", "location_source": "named"})
        self.assertEqual(result.data["result"]["resolved_location"], "江西省·南昌·西湖")
        self.assertEqual(fake.requests[0].url.params["location"], "西湖")

    async def test_proxy_not_configured_is_reported_as_unavailable_weather(self):
        handler = build_handler(FakeQWeather(status=503, error_code="weather_not_configured"))
        result = await handler.execute({"location": "杭州", "location_source": "named"})
        self.assertEqual(result.data["error_code"], "weather_not_configured")
        self.assertIn("weather tool returned no usable result", handler.format_llm_context(result))

    async def test_upstream_failure_is_reported(self):
        handler = build_handler(FakeQWeather(status=502, error_code="upstream_auth_failed"))
        result = await handler.execute({"location": "杭州", "location_source": "named"})
        self.assertEqual(result.data["error_code"], "upstream_auth_failed")
        self.assertIsNone(handler.build_content_block(result, "blk", "log"))


def build_amap_row():
    tools = ["maps_text_search", "maps_search_detail", "maps_geo", "maps_regeocode"]
    tools += ["maps_direction_walking", "maps_direction_driving", "maps_direction_transit_integrated"]
    tools += ["maps_direction_bicycling", "maps_around_search", "maps_distance", "maps_coordinate_convert"]
    return build_row(
        id="amap",
        name="高德地图",
        provider="amap",
        endpoint_url="http://agent-tools-gateway:8100/amap/mcp",
        auth_type="bearer",
        auth_name=None,
        credential_ref="AGENT_TOOLS_API_KEY",
        allowed_tools=tools,
        discovered_tools=[{"name": name, "description": name, "input_schema": {"type": "object"}} for name in tools],
    )


class QWeatherRegistrationTests(unittest.TestCase):
    def load(self, qweather_client):
        repository = FakeRepository([build_amap_row()])
        return load_mcp_agent_tools(
            object(),
            client_manager=FakeClientManager(),
            session_factory=FakeDb,
            repository_factory=lambda _db: repository,
            qweather_client=qweather_client,
        )

    def test_qweather_provides_the_only_weather_tool(self):
        with_qweather = self.load(QWeatherClient())
        self.assertIsInstance(with_qweather.handlers["weather_forecast"], QWeatherForecastToolHandler)
        self.assertIn("local_place_search", with_qweather.handlers)
        self.assertEqual(
            [name for name in (d["function"]["name"] for d in with_qweather.definitions)].count("weather_forecast"), 1
        )
        providers = {b["alias"]: b["provider"] for b in with_qweather.audit_bindings}
        self.assertEqual(providers["weather_forecast"], "qweather")

    def test_without_qweather_no_weather_tool_is_registered(self):
        with patch.object(settings, "AGENT_TOOLS_API_KEY", ""):
            without = self.load(None)
        self.assertNotIn("weather_forecast", without.handlers)
        self.assertIn("local_place_search", without.handlers)

    def test_weather_client_is_built_only_with_the_product_key(self):
        with patch.object(settings, "AGENT_TOOLS_API_KEY", "  "):
            self.assertIsNone(_build_qweather_client())
        with patch.object(settings, "AGENT_TOOLS_API_KEY", "fusion-service-key"):
            client = _build_qweather_client()
        self.assertIsInstance(client, QWeatherClient)
        self.assertEqual(client.timeout_seconds, max(1.0, settings.QWEATHER_TIMEOUT_SECONDS))


if __name__ == "__main__":
    unittest.main()
