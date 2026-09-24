import unittest
from datetime import date, datetime, timezone

from app.schemas.chat import (
    PlaceResult,
    PlaceResultsBlock,
    RouteEndpoint,
    RouteOption,
    RouteResultsBlock,
    TransitLeg,
    WeatherForecastDay,
    WeatherResultsBlock,
)
from app.services.stream.product_answer_validator import validate_product_answer


def _place_block():
    return PlaceResultsBlock(
        type="place_results",
        schema_version=1,
        provider="amap",
        query="烤肉",
        near="深圳民治",
        status="success",
        result_count=1,
        places=[PlaceResult(name="炭火一号", rating=4.7, reference_cost_yuan=88)],
        limitations=["不包含实时排队或空位信息", "参考消费不代表人均或实时价格"],
    )


def _two_place_block():
    block = _place_block()
    block.result_count = 2
    block.places.append(PlaceResult(name="金杆桌球", rating=4.1))
    return block


def _route_block():
    return RouteResultsBlock(
        type="route_results",
        schema_version=1,
        provider="amap",
        status="success",
        origin=RouteEndpoint(label="民治站"),
        destination=RouteEndpoint(label="雅宝站"),
        routes=[
            RouteOption(mode="driving", duration_s=840, distance_m=6200, toll_yuan=5),
            RouteOption(
                mode="transit",
                transit_type="subway",
                duration_s=1920,
                walking_distance_m=420,
                transfers=1,
                legs=[
                    TransitLeg(
                        kind="subway",
                        line_name="地铁5号线",
                        departure_stop="民治站",
                        arrival_stop="五和站",
                        entrance="A口",
                    ),
                    TransitLeg(
                        kind="subway",
                        line_name="地铁10号线",
                        departure_stop="五和站",
                        arrival_stop="雅宝站",
                        exit="D口",
                    ),
                ],
            ),
        ],
        limitations=["路线时间和距离仅代表高德本次返回结果"],
    )


def _weather_block():
    return WeatherResultsBlock(
        type="weather_results",
        schema_version=1,
        provider="amap",
        status="degraded",
        query="南山区",
        resolved_location="南山区",
        day_count=2,
        forecast_days=[
            WeatherForecastDay(
                date=date(2026, 7, 23),
                weekday=4,
                day_weather="多云",
                night_weather="阵雨",
                high_c=32,
                low_c=27,
                day_wind_direction="南",
                day_wind_power="≤3",
                night_wind_direction="东南",
                night_wind_power="≤3",
            ),
            WeatherForecastDay(
                date=date(2026, 7, 24),
                weekday=5,
                day_weather="雷阵雨",
                night_weather="多云",
                high_c=31,
                low_c=26,
            ),
        ],
        fetched_at=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
        limitations=["天气预报按行政区提供，不代表具体建筑物", "仅返回 2 天有效预报"],
    )


def _four_day_weather_block():
    block = _weather_block()
    payload = block.model_dump(mode="python")
    payload["status"] = "success"
    payload["day_count"] = 4
    payload["forecast_days"].extend(
        [
            WeatherForecastDay(
                date=date(2026, 7, 25),
                weekday=6,
                day_weather="晴",
                night_weather="多云",
                high_c=30,
                low_c=25,
            ),
            WeatherForecastDay(
                date=date(2026, 7, 26),
                weekday=7,
                day_weather="多云",
                night_weather="阴",
                high_c=29,
                low_c=24,
            ),
        ]
    )
    return WeatherResultsBlock.model_validate(payload)


def _travel_candidate_blocks() -> list[dict]:
    return [
        {
            "type": "flight_results",
            "id": "flight-outbound",
            "origin": "深圳",
            "destination": "上海",
            "departure_date": "2026-08-01",
            "flights": [
                {
                    "option_id": "flight-a",
                    "flight_no": "ZH1001",
                    "departure": {
                        "city": "深圳",
                        "station_name": "深圳宝安国际机场",
                        "scheduled_at": "2026-08-01T08:00:00+08:00",
                    },
                    "arrival": {
                        "city": "上海",
                        "station_name": "上海虹桥国际机场",
                        "scheduled_at": "2026-08-01T10:00:00+08:00",
                    },
                    "duration_s": 7200,
                    "price": {"currency": "CNY", "amount_minor": 60000},
                },
                {
                    "option_id": "flight-b",
                    "flight_no": "ZH1002",
                    "departure": {
                        "city": "深圳",
                        "station_name": "深圳宝安国际机场",
                        "scheduled_at": "2026-08-01T11:00:00+08:00",
                    },
                    "arrival": {
                        "city": "上海",
                        "station_name": "上海浦东国际机场",
                        "scheduled_at": "2026-08-01T13:30:00+08:00",
                    },
                    "duration_s": 9000,
                    "price": {"currency": "CNY", "amount_minor": 50000},
                },
            ],
        },
        {
            "type": "flight_results",
            "id": "flight-return",
            "origin": "上海",
            "destination": "深圳",
            "departure_date": "2026-08-03",
            "flights": [
                {
                    "option_id": "flight-return-a",
                    "flight_no": "ZH2001",
                    "departure": {
                        "city": "上海",
                        "station_name": "上海虹桥国际机场",
                        "scheduled_at": "2026-08-03T18:00:00+08:00",
                    },
                    "arrival": {
                        "city": "深圳",
                        "station_name": "深圳宝安国际机场",
                        "scheduled_at": "2026-08-03T20:20:00+08:00",
                    },
                    "duration_s": 8400,
                    "price": {"currency": "CNY", "amount_minor": 70000},
                }
            ],
        },
    ]


class ProductAnswerValidatorTests(unittest.TestCase):
    """覆盖收窄后的产品回答校验：只做「固定格式标识符 vs 返回集合」的比对。

    #71 删除了把模型散文解析回事实的那一层（天气结论、数值比对、比较关系、
    意图猜测、分句改写）。留下的判据都不解析自然语言表述，只提取车次号、时刻、
    线路名、站名、出入口、地名这类固定格式串，再跟本轮返回的集合比。
    """

    def _valid(self, answer, blocks):
        return validate_product_answer(answer, blocks)

    # --- 结构性拒绝 ---

    def test_empty_or_non_semantic_answer_is_rejected(self):
        for answer in ("", "   ", "。。。", "!!!"):
            with self.subTest(answer=answer):
                result = self._valid(answer, [_place_block()])
                self.assertFalse(result.is_valid)
                self.assertEqual(result.reason_code, "empty_answer")

    def test_markdown_table_is_rejected(self):
        answer = "| 方案 | 时长 |\n| --- | --- |\n| 驾车 | 14 分钟 |"
        result = self._valid(answer, [_route_block()])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unsupported_format")

    def test_answer_without_product_block_is_rejected(self):
        result = self._valid("炭火一号评分 4.7。", [{"type": "text", "text": "无结构化结果"}])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "missing_product_result")

    # --- 线路 / 站点：出现返回集合以外的标识符即拦 ---

    def test_unknown_subway_line_is_rejected(self):
        result = self._valid("换乘地铁9号线即可到达。", [_route_block()])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_line")

    def test_returned_subway_lines_pass(self):
        result = self._valid("先坐地铁5号线，再换地铁10号线。", [_route_block()])
        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason_code, "ok")

    def test_unknown_transit_stop_is_rejected(self):
        result = self._valid("在白石龙站换乘即可。", [_route_block()])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_route_entity")

    def test_returned_stops_and_exit_pass(self):
        result = self._valid("民治站上车，五和站换乘，雅宝站 D口出站。", [_route_block()])
        self.assertTrue(result.is_valid)

    # --- 航班：车次号 / 站名 / 时刻 / 星期 ---

    def test_unknown_flight_number_is_rejected(self):
        result = self._valid("推荐 ZH9999 这班。", _travel_candidate_blocks())
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_travel_number")

    def test_returned_flight_number_passes(self):
        result = self._valid("推荐 ZH1001 这班。", _travel_candidate_blocks())
        self.assertTrue(result.is_valid)

    def test_unknown_airport_is_rejected(self):
        result = self._valid("ZH1001 从深圳南头机场起飞。", _travel_candidate_blocks())
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_travel_entity")

    def test_unknown_clock_time_is_rejected(self):
        result = self._valid("ZH1001 在 07:15 起飞。", _travel_candidate_blocks())
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_travel_time")

    def test_returned_clock_time_passes(self):
        result = self._valid("ZH1001 在 08:00 起飞。", _travel_candidate_blocks())
        self.assertTrue(result.is_valid)

    # --- 地点：推荐或陈述返回集合以外的店名即拦 ---

    def test_unknown_recommended_place_is_rejected(self):
        result = self._valid("推荐老村长烤肉。", [_place_block()])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "unknown_place")

    def test_returned_place_passes(self):
        result = self._valid("推荐炭火一号。", [_place_block()])
        self.assertTrue(result.is_valid)

    # --- 删除判据后不再拦截的形态：如实固定，避免被当成回归 ---

    def test_prose_claims_without_identifiers_are_no_longer_blocked(self):
        """#71 删除散文解析层后，这些回答一律放行。

        它们曾分别由已删除的判据拦下（实时性断言、无范围最高级、从天气推断活动、
        缺少关系免责句、数值比对）。现在不再拦，风险改由前置事实边界与
        #146 的锚点机制承担；这条测试只固定「确实不拦了」，不是认可这些回答。
        """

        cases = [
            ("炭火一号现在无需排队，保证有空位。", [_place_block()]),
            ("炭火一号是本区最好吃的烤肉店。", [_place_block()]),
            ("明天最高 32℃，适合骑行。", [_weather_block()]),
            ("驾车比地铁快一个小时。", [_route_block()]),
            ("ZH1001 是夜间航班，可以省住宿费。", _travel_candidate_blocks()),
        ]
        for answer, blocks in cases:
            with self.subTest(answer=answer):
                self.assertTrue(self._valid(answer, blocks).is_valid)


if __name__ == "__main__":
    unittest.main()
