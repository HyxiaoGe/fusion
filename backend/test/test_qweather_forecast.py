import base64
import json
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.services.mcp.agent_tools import load_mcp_agent_tools  # noqa: E402
from app.services.weather.qweather_client import (  # noqa: E402
    QWeatherCity,
    QWeatherClient,
    QWeatherCredentials,
    QWeatherError,
    sign_qweather_jwt,
)
from app.services.weather.qweather_forecast_tool import (  # noqa: E402
    QWeatherForecastToolHandler,
    _ForecastCache,
    _Unresolved,
    build_qweather_binding,
    city_label,
    select_city,
)
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository, build_row  # noqa: E402

PRIVATE_KEY = Ed25519PrivateKey.generate()
PEM = PRIVATE_KEY.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
SINGLE_LINE = "".join(line for line in PEM.splitlines() if not line.startswith("-----"))
SHANGHAI = ZoneInfo("Asia/Shanghai")


def credentials(**overrides):
    values = dict(
        api_host="abc.re.qweatherapi.com",
        key_id="KID",
        project_id="PROJ",
        developer_id="DEV",
        private_key=SINGLE_LINE,
    )
    values.update(overrides)
    return QWeatherCredentials.from_settings(**values)


def city(name, adm2, adm1, country="中国", location_id=None, rank=15):
    return QWeatherCity(
        location_id=location_id or f"id-{adm1}-{adm2}-{name}",
        name=name,
        adm1=adm1,
        adm2=adm2,
        country=country,
        rank=rank,
    )


def b64decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


DAILY = [
    {
        "fxDate": f"2026-10-{day:02d}",
        "textDay": "晴",
        "textNight": "多云",
        "tempMax": "26",
        "tempMin": "17",
        "windDirDay": "东风",
        "windDirNight": "北风",
        "windScaleDay": "1-3",
        "windScaleNight": "1-3",
    }
    for day in range(8, 15)
]


HOURLY = [
    {
        "fxTime": f"2026-10-{8 + (16 + hour) // 24:02d}T{(16 + hour) % 24:02d}:00+08:00",
        "temp": str(20 - hour // 4),
        "text": "小雨" if 3 <= hour <= 5 else "多云",
        "pop": str(70 if 3 <= hour <= 5 else 10),
        "precip": "0.6" if 3 <= hour <= 5 else "0.0",
    }
    for hour in range(24)
]


class FakeQWeather:
    def __init__(self, *, cities=None, daily=None, status=200, hourly_status=200):
        self.cities = cities if cities is not None else [city("杭州", "杭州", "浙江省", location_id="101210101")]
        self.daily = daily if daily is not None else DAILY
        self.status = status
        self.hourly_status = hourly_status
        self.requests = []

    def transport(self):
        def handle(request):
            self.requests.append(request)
            if self.status != 200:
                return httpx.Response(self.status, json={"code": str(self.status)})
            if request.url.path == "/geo/v2/city/lookup":
                return httpx.Response(
                    200,
                    json={
                        "code": "200",
                        "location": [
                            {
                                "id": c.location_id,
                                "name": c.name,
                                "adm1": c.adm1,
                                "adm2": c.adm2,
                                "country": c.country,
                                "rank": str(c.rank),
                            }
                            for c in self.cities
                        ],
                    },
                )
            if request.url.path == "/v7/weather/24h":
                if self.hourly_status != 200:
                    return httpx.Response(self.hourly_status, json={"code": str(self.hourly_status)})
                return httpx.Response(200, json={"code": "200", "hourly": HOURLY})
            return httpx.Response(200, json={"code": "200", "daily": self.daily})

        return httpx.MockTransport(handle)


def build_handler(fake, **kwargs):
    client = QWeatherClient(credentials(), transport=fake.transport())
    return QWeatherForecastToolHandler(
        binding=build_qweather_binding(),
        client=client,
        cache=_ForecastCache(),
        now=lambda: datetime(2026, 10, 8, 16, 0, tzinfo=SHANGHAI),
        **kwargs,
    )


class QWeatherCredentialTests(unittest.TestCase):
    def test_incomplete_settings_disable_and_invalid_key_raises(self):
        self.assertIsNone(credentials(private_key=""))
        self.assertIsNone(credentials(key_id=" "))
        with self.assertRaises(ValueError):
            credentials(private_key="not-a-key")
        with self.assertRaises(ValueError):
            credentials(api_host="evil.example.com")
        self.assertIsNotNone(credentials(private_key=PEM))

    def test_jwt_has_only_documented_fields_and_valid_signature(self):
        token = sign_qweather_jwt(credentials(), issued_at=1_000)
        header, payload, signature = token.split(".")
        self.assertEqual(json.loads(b64decode(header)), {"alg": "EdDSA", "kid": "KID"})
        self.assertEqual(
            json.loads(b64decode(payload)),
            {"iss": "DEV", "sub": "PROJ", "iat": 1_000, "exp": 1_900},
        )
        PRIVATE_KEY.public_key().verify(b64decode(signature), f"{header}.{payload}".encode())


class QWeatherClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_is_reused_until_refresh_margin(self):
        fake = FakeQWeather()
        clock = SimpleNamespace(value=10_000.0)
        client = QWeatherClient(credentials(), transport=fake.transport(), clock=lambda: clock.value)

        await client.lookup_city("杭州")
        await client.lookup_city("杭州")
        clock.value += 800
        await client.lookup_city("杭州")

        tokens = [request.headers["authorization"] for request in fake.requests]
        self.assertEqual(tokens[0], tokens[1])
        self.assertNotEqual(tokens[1], tokens[2])
        self.assertEqual(fake.requests[0].url.host, "abc.re.qweatherapi.com")

    async def test_http_errors_map_to_codes(self):
        for status, code in (
            (401, "upstream_auth_failed"),
            (429, "upstream_rate_limited"),
            (500, "upstream_unavailable"),
        ):
            with self.subTest(status=status):
                client = QWeatherClient(credentials(), transport=FakeQWeather(status=status).transport())
                with self.assertRaises(QWeatherError) as raised:
                    await client.daily_forecast("101210101")
                self.assertEqual(raised.exception.code, code)


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


class QWeatherHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_location_returns_four_day_block(self):
        fake = FakeQWeather()
        handler = build_handler(fake)

        result = await handler.execute({"location": "杭州", "location_source": "named", "requested_date": "2026-10-10"})

        self.assertEqual(result.status, "success")
        block = handler.build_content_block(result, "blk", "log")
        self.assertEqual(block.provider, "qweather")
        self.assertEqual(block.attribution.label, "和风天气")
        self.assertEqual(block.resolved_location, "浙江省·杭州")
        self.assertEqual(block.day_count, 4)
        self.assertEqual(block.forecast_days[0].day_wind_power, "1-3级")
        self.assertEqual(block.forecast_days[0].weekday, 4)
        self.assertEqual(str(block.requested_date), "2026-10-10")
        self.assertIn("forecast_days", handler.format_llm_context(result))
        self.assertEqual(fake.requests[-1].url.params["location"], "101210101")

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
                self.assertEqual(any(r.url.path == "/v7/weather/24h" for r in fake.requests), expected)

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
        forecast_calls = [r for r in fake.requests if r.url.path == "/v7/weather/7d"]
        self.assertEqual(len(forecast_calls), 1)

    async def test_current_location_uses_coordinates(self):
        fake = FakeQWeather()
        handler = build_handler(fake)
        context = SimpleNamespace(geolocation=SimpleNamespace(latitude=30.2741, longitude=120.1551))

        result = await handler.execute_with_runtime_context(
            {"location": "current_location", "location_source": "current_location"}, context
        )

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
        self.assertEqual([r.url.path for r in fake.requests], ["/geo/v2/city/lookup"])

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

    async def test_lookup_bad_request_means_not_found(self):
        handler = build_handler(FakeQWeather(status=400))
        result = await handler.execute({"location": "鼓浪屿", "location_source": "named"})
        self.assertEqual(result.data["error_code"], "location_not_found")

    async def test_upstream_failure_is_reported(self):
        handler = build_handler(FakeQWeather(status=401))
        result = await handler.execute({"location": "杭州", "location_source": "named"})
        self.assertEqual(result.data["error_code"], "upstream_auth_failed")
        self.assertIsNone(handler.build_content_block(result, "blk", "log"))


def build_amap_row():
    tools = ["maps_text_search", "maps_search_detail", "maps_geo", "maps_regeocode", "maps_weather"]
    tools += ["maps_direction_walking", "maps_direction_driving", "maps_direction_transit_integrated"]
    tools += ["maps_direction_bicycling", "maps_around_search", "maps_distance"]
    return build_row(
        id="amap",
        name="高德地图",
        provider="amap",
        endpoint_url="https://mcp.amap.com/mcp",
        auth_type="query",
        auth_name="key",
        credential_ref="AMAP_MCP_API_KEY",
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

    def test_qweather_replaces_amap_weather_only(self):
        with_qweather = self.load(QWeatherClient(credentials()))
        self.assertIsInstance(with_qweather.handlers["weather_forecast"], QWeatherForecastToolHandler)
        self.assertIn("local_place_search", with_qweather.handlers)
        self.assertEqual(
            [name for name in (d["function"]["name"] for d in with_qweather.definitions)].count("weather_forecast"), 1
        )
        providers = {b["alias"]: b["provider"] for b in with_qweather.audit_bindings}
        self.assertEqual(providers["weather_forecast"], "qweather")

    def test_without_qweather_amap_keeps_weather(self):
        without = self.load(None)
        self.assertNotIsInstance(without.handlers.get("weather_forecast"), QWeatherForecastToolHandler)


if __name__ == "__main__":
    unittest.main()
