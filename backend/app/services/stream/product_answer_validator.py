"""校验模型基于结构化产品结果生成的最终回答。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class ProductAnswerValidation:
    """只返回稳定原因码，避免日志或调用方持有模型原文。"""

    is_valid: bool
    reason_code: str


_PRODUCT_RESULT_TYPES = {
    "place_results",
    "route_results",
    "weather_results",
    "flight_results",
    "train_results",
    "itinerary_results",
}
_LINE_RE = re.compile(
    r"(?:地铁|轨道交通)?\s*(?:\d+|[一二三四五六七八九十百]+)\s*号线|"
    r"(?:地铁|轨道交通)\s*(?:\d+|[一二三四五六七八九十百]+)\s*线|"
    r"高峰专线\s*\d+\s*号?|(?:[A-Za-z]\d{1,4})(?:路|线)|\d{1,4}路",
    re.IGNORECASE,
)
_EXPLICIT_RECOMMENDATION_RE = re.compile(
    r"(?:首选|推荐|优先选择|建议选择|可以去|选择)\s*[「『“\"']?([^，。；！？!\n]{2,48})"
)
_ROUTE_MODE_TERMS = {
    "driving": ("驾车", "开车", "自驾"),
    "transit": ("公交", "地铁", "公共交通", "轨道交通"),
    "walking": ("步行", "走路"),
    "bicycling": ("骑行", "单车", "自行车"),
}
_ROUTE_STATION_MENTION_RE = re.compile(
    r"(?:在|从|到|至|经|由|途经|抵达|前往|经过)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()]{1,24}?站)"
)
_ROUTE_STATION_ACTION_RE = re.compile(
    r"(?:^|[，,。！？!?；;\s])"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()]{1,24}?站)"
    r"(?=换乘|转乘|乘坐|上车|下车)"
)
_ROUTE_ACCESS_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<name>(?:[A-Za-z]\d{0,2}|[东南西北])\s*(?:出入口|入口|出口|口))",
    re.IGNORECASE,
)
_GENERIC_ROUTE_STATIONS = {"站", "车站", "公交站", "地铁站", "进站", "出站", "到站"}
_DIRECT_PLACE_FACT_RE = re.compile(
    r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9·（）()]{2,32})(?:的)?"
    r"(?:综合)?(?:评分|参考消费|距离)"
)
_GENERIC_PLACE_REFERENCES = {"这家店", "该店", "这个地点", "该地点", "第一家", "第二家", "第三家"}
_PLACE_NAME_SUFFIX_RE = re.compile(r"(?:店|馆|中心|公园|站|广场|城|吧|餐厅|咖啡|火锅)$")
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$",
    re.MULTILINE,
)
_TRAVEL_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{3,4}|[GDCZTKSY]\d{1,5})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TRAVEL_STATION_MENTION_RE = re.compile(
    r"(?:到达|抵达|前往|从|到|在|由)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()]{2,32}?(?:国际机场|机场|火车站|高铁站|站))"
)
_CLOCK_TIME_RE = re.compile(r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?!\d)")
_TRAVEL_WEEKDAY_RE = re.compile(r"(?:星期|周)(?P<day>[一二三四五六日天])")
_WEATHER_FACT_CUE_RE = re.compile(r"气温|温度|最高|最低|雨|雪|雷|多云|阴|晴|雾|霾|风|防晒|保暖|加衣|雨具")
_WEATHER_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_WEATHER_COMPACT_RANGE_RE = re.compile(
    r"(?P<month>\d{1,2})月(?P<start>\d{1,2})(?:日)?\s*[-~至到～—]\s*(?P<end>\d{1,2})日"
)
_WEATHER_DAY_ONLY_RE = re.compile(r"(?<!月)(?<!\d)(?P<day>\d{1,2})日")
_WEATHER_RELATIVE_DAY_RE = re.compile(r"明后天|大后天|今晚|今夜|今早|今晨|明晚|明夜|明早|明晨|今天|明天|后天")
_WEATHER_RELATIVE_DAY_OFFSETS = {
    "今天": (0,),
    "今晚": (0,),
    "今夜": (0,),
    "今早": (0,),
    "今晨": (0,),
    "明天": (1,),
    "明晚": (1,),
    "明夜": (1,),
    "明早": (1,),
    "明晨": (1,),
    "后天": (2,),
    "大后天": (3,),
    "明后天": (1, 2),
}
_SEMANTIC_TEXT_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _empty_numeric_values() -> dict[str, set[float]]:
    return {
        "duration_minutes": set(),
        "distance_m": set(),
        "walking_distance_m": set(),
        "money_yuan": set(),
        "reference_cost_yuan": set(),
        "toll_yuan": set(),
        "transfers": set(),
        "rating": set(),
    }


@dataclass
class _FactIndex:
    searched_place_names: set[str]
    entity_names: set[str]
    route_lines: set[str]
    route_stop_names: set[str]
    route_access_names: set[str]
    numeric_values: dict[str, set[float]]
    route_numeric_values: dict[str, dict[str, set[float]]]
    route_primary_numeric_values: dict[str, dict[str, set[float]]]
    place_numeric_values: dict[str, dict[str, set[float]]]
    route_endpoint_pairs: set[frozenset[str]]
    has_place_results: bool
    has_route_results: bool
    has_travel_results: bool
    travel_numbers: set[str]
    travel_station_names: set[str]
    travel_clock_times: set[str]
    travel_weekdays: set[str]
    travel_candidates: list["_TravelCandidateFacts"]
    has_weather_results: bool
    weather_days: list["_WeatherDayFacts"]
    weather_locations: set[str]


@dataclass(frozen=True)
class _WeatherDayFacts:
    date: str
    weekday: int
    day_weather: str
    night_weather: str
    high_c: float
    low_c: float
    day_wind_direction: str | None
    night_wind_direction: str | None
    day_wind_power: str | None
    night_wind_power: str | None


@dataclass(frozen=True)
class _TravelCandidateFacts:
    direction: str
    number: str
    identifiers: frozenset[str]
    station_names: frozenset[str]
    clock_times: frozenset[str]
    duration_minutes: float | None
    price_yuan: float | None


def validate_product_answer(
    answer: str,
    content_blocks: list[Any],
    *,
    messages: list[dict] | None = None,
) -> ProductAnswerValidation:
    """验证高置信硬事实；无法可靠判断的自然语言交给前置事实边界约束。"""

    normalized_answer = answer.strip() if isinstance(answer, str) else ""
    if not normalized_answer or not _SEMANTIC_TEXT_RE.search(normalized_answer):
        return ProductAnswerValidation(False, "empty_answer")
    if _MARKDOWN_TABLE_SEPARATOR_RE.search(normalized_answer):
        return ProductAnswerValidation(False, "unsupported_format")

    product_blocks = [block for block in content_blocks if _value(block, "type") in _PRODUCT_RESULT_TYPES]
    if not product_blocks:
        return ProductAnswerValidation(False, "missing_product_result")

    facts = _build_fact_index(product_blocks)
    if _has_unknown_line(normalized_answer, facts.route_lines):
        return ProductAnswerValidation(False, "unknown_line")
    if facts.has_route_results and _has_unknown_route_entity(
        normalized_answer,
        allowed_stops=facts.route_stop_names,
        allowed_accesses=facts.route_access_names,
    ):
        return ProductAnswerValidation(False, "unknown_route_entity")
    if facts.has_travel_results and _has_unknown_travel_number(normalized_answer, facts.travel_numbers):
        return ProductAnswerValidation(False, "unknown_travel_number")
    if facts.has_travel_results and _has_unknown_travel_entity(normalized_answer, facts.travel_station_names):
        return ProductAnswerValidation(False, "unknown_travel_entity")
    if facts.has_travel_results and _has_unknown_travel_time(normalized_answer, facts.travel_clock_times):
        return ProductAnswerValidation(False, "unknown_travel_time")
    if facts.has_travel_results and _has_unknown_travel_weekday(
        normalized_answer,
        facts.travel_weekdays,
        facts.weather_days,
    ):
        return ProductAnswerValidation(False, "unknown_travel_date")
    if facts.searched_place_names and _has_unknown_recommended_place(
        normalized_answer,
        facts.searched_place_names,
    ):
        return ProductAnswerValidation(False, "unknown_place")
    if facts.searched_place_names and _has_unknown_place_fact(
        normalized_answer,
        facts.searched_place_names,
    ):
        return ProductAnswerValidation(False, "unknown_place")
    return ProductAnswerValidation(True, "ok")


def _build_fact_index(blocks: list[Any]) -> _FactIndex:
    searched_place_names: set[str] = set()
    entity_names: set[str] = set()
    route_lines: set[str] = set()
    route_stop_names: set[str] = set()
    route_access_names: set[str] = set()
    numeric_values = _empty_numeric_values()
    route_numeric_values: dict[str, dict[str, set[float]]] = {}
    route_primary_numeric_values: dict[str, dict[str, set[float]]] = {}
    place_numeric_values: dict[str, dict[str, set[float]]] = {}
    route_endpoint_pairs: set[frozenset[str]] = set()
    has_place_results = False
    has_route_results = False
    has_travel_results = False
    travel_numbers: set[str] = set()
    travel_station_names: set[str] = set()
    travel_clock_times: set[str] = set()
    travel_weekdays: set[str] = set()
    travel_candidates: list[_TravelCandidateFacts] = []
    has_weather_results = False
    weather_days: list[_WeatherDayFacts] = []
    weather_locations: set[str] = set()

    travel_directions = _travel_block_directions(blocks)

    for block in blocks:
        block_type = _value(block, "type")
        if block_type == "place_results":
            has_place_results = True
            for place in (_value(block, "places") or [])[:10]:
                name = _value(place, "name")
                _add_text(searched_place_names, name)
                _add_text(entity_names, name)
                place_values = place_numeric_values.setdefault(
                    _compact_text(name) if isinstance(name, str) else "",
                    _empty_numeric_values(),
                )
                _add_scoped_number(numeric_values, place_values, "distance_m", _value(place, "distance_m"))
                _add_scoped_number(
                    numeric_values,
                    place_values,
                    "money_yuan",
                    _value(place, "reference_cost_yuan"),
                )
                _add_scoped_number(
                    numeric_values,
                    place_values,
                    "reference_cost_yuan",
                    _value(place, "reference_cost_yuan"),
                )
                _add_scoped_number(numeric_values, place_values, "rating", _value(place, "rating"))
            continue

        if block_type in {"flight_results", "train_results"}:
            has_travel_results = True
            travel_weekdays.update(_weekday_tokens(_value(block, "departure_date")))
            collection = "flights" if block_type == "flight_results" else "trains"
            number_key = "flight_no" if block_type == "flight_results" else "train_no"
            direction = travel_directions.get(str(_value(block, "id") or ""), "outbound")
            for option in (_value(block, collection) or [])[:5]:
                number = _value(option, number_key)
                if isinstance(number, str) and number.strip():
                    travel_numbers.add(number.strip().upper())
                duration_s = _number(_value(option, "duration_s"))
                if duration_s is not None:
                    numeric_values["duration_minutes"].add(duration_s / 60)
                price_minor = _number(_value(_value(option, "price"), "amount_minor"))
                if price_minor is not None:
                    numeric_values["money_yuan"].add(price_minor / 100)
                candidate_stations: set[str] = set()
                candidate_times: set[str] = set()
                candidate_identifiers = (
                    {number.strip().upper()} if isinstance(number, str) and number.strip() else set()
                )
                for endpoint_key in ("departure", "arrival"):
                    endpoint = _value(option, endpoint_key)
                    _add_text(entity_names, _value(endpoint, "city"))
                    station_name = _value(endpoint, "station_name")
                    _add_text(entity_names, station_name)
                    _add_text(travel_station_names, station_name)
                    if isinstance(station_name, str) and station_name.strip():
                        candidate_stations.add(_canonical_travel_station(station_name))
                    terminal = _value(endpoint, "terminal")
                    if isinstance(terminal, str) and terminal.strip():
                        travel_numbers.add(terminal.strip().upper())
                        candidate_identifiers.add(terminal.strip().upper())
                    scheduled_at = _value(endpoint, "scheduled_at")
                    clock_time = _clock_time(scheduled_at)
                    if clock_time:
                        travel_clock_times.add(clock_time)
                        candidate_times.add(clock_time)
                if isinstance(number, str) and number.strip():
                    travel_candidates.append(
                        _TravelCandidateFacts(
                            direction=direction,
                            number=number.strip().upper(),
                            identifiers=frozenset(candidate_identifiers),
                            station_names=frozenset(candidate_stations),
                            clock_times=frozenset(candidate_times),
                            duration_minutes=duration_s / 60 if duration_s is not None else None,
                            price_yuan=price_minor / 100 if price_minor is not None else None,
                        )
                    )
            continue

        if block_type == "itinerary_results":
            for plan in (_value(block, "plans") or [])[:2]:
                known_duration_s = _number(_value(plan, "known_duration_s"))
                if known_duration_s is not None:
                    numeric_values["duration_minutes"].add(known_duration_s / 60)
                known_cost_minor = _number(_value(_value(plan, "known_cost"), "amount_minor"))
                if known_cost_minor is not None:
                    numeric_values["money_yuan"].add(known_cost_minor / 100)
            continue

        if block_type == "weather_results":
            has_weather_results = True
            _add_text(weather_locations, _value(block, "resolved_location"))
            for day in (_value(block, "forecast_days") or [])[:4]:
                raw_date = _value(day, "date")
                date_text = raw_date.isoformat() if hasattr(raw_date, "isoformat") else str(raw_date)
                weekday = _value(day, "weekday")
                day_weather = _value(day, "day_weather")
                night_weather = _value(day, "night_weather")
                high_c = _weather_number(_value(day, "high_c"))
                low_c = _weather_number(_value(day, "low_c"))
                if not (
                    re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text)
                    and isinstance(weekday, int)
                    and not isinstance(weekday, bool)
                    and 1 <= weekday <= 7
                    and isinstance(day_weather, str)
                    and day_weather
                    and isinstance(night_weather, str)
                    and night_weather
                    and high_c is not None
                    and low_c is not None
                ):
                    continue
                weather_days.append(
                    _WeatherDayFacts(
                        date=date_text,
                        weekday=weekday,
                        day_weather=day_weather,
                        night_weather=night_weather,
                        high_c=high_c,
                        low_c=low_c,
                        day_wind_direction=_optional_weather_text(_value(day, "day_wind_direction")),
                        night_wind_direction=_optional_weather_text(_value(day, "night_wind_direction")),
                        day_wind_power=_optional_weather_text(_value(day, "day_wind_power")),
                        night_wind_power=_optional_weather_text(_value(day, "night_wind_power")),
                    )
                )
            continue

        if block_type != "route_results":
            continue
        has_route_results = True
        origin = _value(_value(block, "origin"), "label")
        destination = _value(_value(block, "destination"), "label")
        _add_text(entity_names, origin)
        _add_text(entity_names, destination)
        _add_text(route_stop_names, origin)
        _add_text(route_stop_names, destination)
        if isinstance(origin, str) and origin.strip() and isinstance(destination, str) and destination.strip():
            route_endpoint_pairs.add(frozenset({_compact_text(origin), _compact_text(destination)}))
        for route in (_value(block, "routes") or [])[:6]:
            _collect_route_facts(
                route,
                route_lines,
                route_stop_names,
                route_access_names,
                numeric_values,
                route_numeric_values,
                route_primary_numeric_values,
                parent_mode=None,
            )

    return _FactIndex(
        searched_place_names=searched_place_names,
        entity_names=entity_names,
        route_lines=route_lines,
        route_stop_names=route_stop_names,
        route_access_names=route_access_names,
        numeric_values=numeric_values,
        route_numeric_values=route_numeric_values,
        route_primary_numeric_values=route_primary_numeric_values,
        place_numeric_values=place_numeric_values,
        route_endpoint_pairs=route_endpoint_pairs,
        has_place_results=has_place_results,
        has_route_results=has_route_results,
        has_travel_results=has_travel_results,
        travel_numbers=travel_numbers,
        travel_station_names=travel_station_names,
        travel_clock_times=travel_clock_times,
        travel_weekdays=travel_weekdays,
        travel_candidates=travel_candidates,
        has_weather_results=has_weather_results,
        weather_days=weather_days,
        weather_locations=weather_locations,
    )


def _travel_block_directions(blocks: list[Any]) -> dict[str, str]:
    """以首个行程方向为去程，并用 itinerary 引用覆盖，避免靠正文猜方向。"""

    travel_blocks = [block for block in blocks if _value(block, "type") in {"flight_results", "train_results"}]
    if not travel_blocks:
        return {}
    first_origin = _compact_text(str(_value(travel_blocks[0], "origin") or ""))
    first_destination = _compact_text(str(_value(travel_blocks[0], "destination") or ""))
    directions: dict[str, str] = {}
    for block in travel_blocks:
        block_id = str(_value(block, "id") or "")
        origin = _compact_text(str(_value(block, "origin") or ""))
        destination = _compact_text(str(_value(block, "destination") or ""))
        directions[block_id] = (
            "return"
            if origin == first_destination and destination == first_origin and first_origin != first_destination
            else "outbound"
        )
    for itinerary in (block for block in blocks if _value(block, "type") == "itinerary_results"):
        for plan in (_value(itinerary, "plans") or [])[:2]:
            for section in _value(plan, "sections") or []:
                kind = _value(section, "kind")
                if kind not in {"outbound_transport", "return_transport"}:
                    continue
                direction = "outbound" if kind == "outbound_transport" else "return"
                for result_ref in _value(section, "result_refs") or []:
                    block_id = str(_value(result_ref, "block_id") or "")
                    if block_id in directions:
                        directions[block_id] = direction
    return directions


def _collect_route_facts(
    route: Any,
    route_lines: set[str],
    route_stop_names: set[str],
    route_access_names: set[str],
    numeric_values: dict[str, set[float]],
    route_numeric_values: dict[str, dict[str, set[float]]],
    route_primary_numeric_values: dict[str, dict[str, set[float]]],
    *,
    parent_mode: str | None,
) -> None:
    mode = _value(route, "mode") or parent_mode
    scoped_values = route_numeric_values.setdefault(mode, _empty_numeric_values()) if mode else None
    primary_values = (
        route_primary_numeric_values.setdefault(mode, _empty_numeric_values()) if mode and parent_mode is None else None
    )
    duration_s = _number(_value(route, "duration_s"))
    if duration_s is not None:
        _add_scoped_value(numeric_values, scoped_values, "duration_minutes", duration_s / 60)
        if primary_values is not None:
            primary_values["duration_minutes"].add(duration_s / 60)
    if mode != "transit":
        _add_scoped_number(numeric_values, scoped_values, "distance_m", _value(route, "distance_m"))
        if primary_values is not None:
            _add_bucket_number(primary_values, "distance_m", _value(route, "distance_m"))
    _add_scoped_number(
        numeric_values,
        scoped_values,
        "walking_distance_m",
        _value(route, "walking_distance_m"),
    )
    _add_scoped_number(numeric_values, scoped_values, "money_yuan", _value(route, "toll_yuan"))
    _add_scoped_number(numeric_values, scoped_values, "toll_yuan", _value(route, "toll_yuan"))
    _add_scoped_number(numeric_values, scoped_values, "transfers", _value(route, "transfers"))

    for leg in (_value(route, "legs") or [])[:12]:
        _add_text(route_lines, _value(leg, "line_name"))
        _add_text(route_stop_names, _value(leg, "departure_stop"))
        _add_text(route_stop_names, _value(leg, "arrival_stop"))
        _add_text(route_access_names, _value(leg, "entrance"))
        _add_text(route_access_names, _value(leg, "exit"))

    for alternative in (_value(route, "alternatives") or [])[:4]:
        _collect_route_facts(
            alternative,
            route_lines,
            route_stop_names,
            route_access_names,
            numeric_values,
            route_numeric_values,
            route_primary_numeric_values,
            parent_mode=mode,
        )


def _weather_scoped_days(
    sentence: str,
    days: list[_WeatherDayFacts],
    *,
    position: int | None = None,
) -> list[_WeatherDayFacts]:
    mentions: list[tuple[int, _WeatherDayFacts]] = []
    weekday_labels = ("一", "二", "三", "四", "五", "六", "日")
    for day in days:
        year, month, calendar_day = day.date.split("-")
        del year
        weekday = weekday_labels[day.weekday - 1]
        tokens = {
            day.date,
            f"{int(month)}月{int(calendar_day)}日",
            f"周{weekday}",
            f"星期{weekday}",
        }
        if weekday == "日":
            tokens.update({"周天", "星期天"})
        for token in tokens:
            start = sentence.find(token)
            while start >= 0:
                mentions.append((start, day))
                start = sentence.find(token, start + len(token))
        if day.weekday in {6, 7}:
            start = sentence.find("周末")
            while start >= 0:
                mentions.append((start, day))
                start = sentence.find("周末", start + 2)
    if days:
        day_by_date = {day.date: day for day in days}
        base_date = datetime.strptime(min(day_by_date), "%Y-%m-%d").date()
        for match in _WEATHER_RELATIVE_DAY_RE.finditer(sentence):
            for offset in _WEATHER_RELATIVE_DAY_OFFSETS[match.group(0)]:
                relative_day = day_by_date.get((base_date + timedelta(days=offset)).isoformat())
                if relative_day is not None:
                    mentions.append((match.start(), relative_day))
        mentions.extend(_weather_compact_range_mentions(sentence, days))
        mentions.extend(_weather_day_only_mentions(sentence, days))
    if position is not None and mentions:
        preceding = [item for item in mentions if item[0] <= position]
        selected_position = max(item[0] for item in preceding) if preceding else min(item[0] for item in mentions)
        return list(dict.fromkeys(day for start, day in mentions if start == selected_position))
    return list(dict.fromkeys(day for _, day in sorted(mentions, key=lambda item: item[0])))


def _weather_compact_range_mentions(
    sentence: str,
    days: list[_WeatherDayFacts],
) -> list[tuple[int, _WeatherDayFacts]]:
    mentions: list[tuple[int, _WeatherDayFacts]] = []
    for match in _WEATHER_COMPACT_RANGE_RE.finditer(sentence):
        mentions.extend((match.start(), day) for day in _weather_compact_range_days(match, days))
    return mentions


def _weather_compact_range_days(
    match: re.Match[str],
    days: list[_WeatherDayFacts],
) -> list[_WeatherDayFacts]:
    month = int(match.group("month"))
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end < start or end - start > 31:
        return []
    by_month_day = {(int(day.date[5:7]), int(day.date[8:10])): day for day in days}
    calendar_days = range(start, end + 1)
    if any((month, calendar_day) not in by_month_day for calendar_day in calendar_days):
        return []
    return [by_month_day[(month, calendar_day)] for calendar_day in calendar_days]


def _weather_day_only_mentions(
    sentence: str,
    days: list[_WeatherDayFacts],
) -> list[tuple[int, _WeatherDayFacts]]:
    """解析“7月29日……，30日……”中的后续日号；没有前置完整日期时保持拒绝。"""

    full_date_starts = [match.start() for match in re.finditer(r"\d{1,2}月\d{1,2}日", sentence)]
    if not full_date_starts:
        return []
    days_by_calendar_day: dict[int, list[_WeatherDayFacts]] = {}
    for day in days:
        days_by_calendar_day.setdefault(int(day.date[8:10]), []).append(day)

    mentions: list[tuple[int, _WeatherDayFacts]] = []
    for match in _WEATHER_DAY_ONLY_RE.finditer(sentence):
        if not any(start < match.start() for start in full_date_starts):
            continue
        candidates = days_by_calendar_day.get(int(match.group("day")), [])
        if len(candidates) == 1:
            mentions.append((match.start(), candidates[0]))
    return mentions


def _has_unknown_line(answer: str, allowed_lines: set[str]) -> bool:
    normalized_lines = {_canonical_line(match.group(0)) for line in allowed_lines for match in _LINE_RE.finditer(line)}
    for match in _LINE_RE.finditer(answer):
        if _canonical_line(match.group(0)) not in normalized_lines:
            return True
    return False


def _has_unknown_route_entity(
    answer: str,
    *,
    allowed_stops: set[str],
    allowed_accesses: set[str],
) -> bool:
    normalized_stops = {_canonical_station_name(value) for value in allowed_stops if _canonical_station_name(value)}
    normalized_accesses = {_canonical_access_name(value) for value in allowed_accesses if _canonical_access_name(value)}
    for pattern in (_ROUTE_STATION_MENTION_RE, _ROUTE_STATION_ACTION_RE):
        for match in pattern.finditer(answer):
            name = match.group("name")
            if name in _GENERIC_ROUTE_STATIONS or name.endswith(("进站", "出站", "到站", "离站")):
                continue
            if _canonical_station_name(name) not in normalized_stops:
                return True
    for match in _ROUTE_ACCESS_MENTION_RE.finditer(answer):
        if _canonical_access_name(match.group("name")) not in normalized_accesses:
            return True
    return False


def _has_unknown_travel_number(answer: str, allowed_numbers: set[str]) -> bool:
    return any(match.group(0).upper() not in allowed_numbers for match in _TRAVEL_NUMBER_RE.finditer(answer))


def _has_unknown_travel_entity(answer: str, allowed_stations: set[str]) -> bool:
    normalized = {_canonical_travel_station(value) for value in allowed_stations}
    for match in _TRAVEL_STATION_MENTION_RE.finditer(answer):
        if _canonical_travel_station(match.group("name")) not in normalized:
            return True
    return False


def _has_unknown_travel_time(answer: str, allowed_times: set[str]) -> bool:
    return any(match.group(0) not in allowed_times for match in _CLOCK_TIME_RE.finditer(answer))


def _has_unknown_travel_weekday(
    answer: str,
    allowed_weekdays: set[str],
    weather_days: list[_WeatherDayFacts],
) -> bool:
    weekday_labels = ("一", "二", "三", "四", "五", "六", "日")
    for sentence in _WEATHER_SENTENCE_SPLIT_RE.split(answer):
        for match in _TRAVEL_WEEKDAY_RE.finditer(sentence):
            weekday = match.group("day").replace("天", "日")
            if weekday in allowed_weekdays:
                continue
            scoped_weather_days = _weather_scoped_days(sentence, weather_days)
            if _WEATHER_FACT_CUE_RE.search(sentence) and any(
                weekday_labels[day.weekday - 1] == weekday for day in scoped_weather_days
            ):
                continue
            return True
    return False


def _weekday_tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    try:
        weekday = datetime.strptime(value, "%Y-%m-%d").weekday()
    except ValueError:
        return set()
    token = ("一", "二", "三", "四", "五", "六", "日")[weekday]
    return {token, "天"} if token == "日" else {token}


def _canonical_travel_station(value: str) -> str:
    return _compact_text(value).replace("国际机场", "机场")


def _clock_time(value: Any) -> str | None:
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    if isinstance(value, str):
        match = re.search(r"T(\d{2}:\d{2})", value)
        if match:
            return match.group(1)
    return None


def _canonical_station_name(value: str) -> str:
    compact = _compact_text(value)
    return re.sub(r"(?:地铁)?站$", "", compact)


def _canonical_access_name(value: str) -> str:
    compact = _compact_text(value).upper()
    return re.sub(r"(?:出入口|入口|出口|口)$", "", compact)


def _canonical_line(value: str) -> str:
    compact = _compact_text(value)
    bus_code_match = re.fullmatch(r"([a-z]\d+)(?:路|线)", compact)
    if bus_code_match:
        return bus_code_match.group(1).upper()
    number_match = re.search(r"(\d+|[一二三四五六七八九十百]+)号?线$", compact)
    if number_match:
        raw_number = number_match.group(1)
        number = raw_number if raw_number.isdigit() else str(_chinese_number(raw_number))
        return f"{number}号线"
    return compact.upper()


def _chinese_number(value: str) -> int:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(value, -1)


def _has_unknown_recommended_place(answer: str, allowed_places: set[str]) -> bool:
    normalized_places = {_compact_text(place) for place in allowed_places}
    for match in _EXPLICIT_RECOMMENDATION_RE.finditer(answer):
        candidate = _compact_text(match.group(1))
        if any(term in candidate for terms in _ROUTE_MODE_TERMS.values() for term in terms):
            continue
        if not any(place in candidate or candidate in place for place in normalized_places):
            return True
    return False


def _has_unknown_place_fact(answer: str, allowed_places: set[str]) -> bool:
    normalized_places = {_compact_text(place) for place in allowed_places}
    for match in _DIRECT_PLACE_FACT_RE.finditer(answer):
        subject = _compact_text(match.group("subject"))
        if subject in {_compact_text(value) for value in _GENERIC_PLACE_REFERENCES}:
            continue
        subject_parts = [part for part in re.split(r"和|与|及|、", subject) if part]
        if len(subject_parts) > 1:
            for part in subject_parts:
                if any(place in part or part in place for place in normalized_places):
                    continue
                if _PLACE_NAME_SUFFIX_RE.search(part):
                    return True
            continue
        if any(place in subject or subject in place for place in normalized_places):
            continue
        if _PLACE_NAME_SUFFIX_RE.search(subject):
            return True
    return False


def _add_scoped_number(
    all_values: dict[str, set[float]],
    scoped_values: dict[str, set[float]] | None,
    category: str,
    value: Any,
) -> None:
    parsed = _number(value)
    if parsed is not None:
        _add_scoped_value(all_values, scoped_values, category, parsed)


def _add_bucket_number(bucket: dict[str, set[float]], category: str, value: Any) -> None:
    parsed = _number(value)
    if parsed is not None:
        bucket[category].add(parsed)


def _add_scoped_value(
    all_values: dict[str, set[float]],
    scoped_values: dict[str, set[float]] | None,
    category: str,
    value: float,
) -> None:
    all_values[category].add(value)
    if scoped_values is not None:
        scoped_values[category].add(value)


def _add_text(values: set[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        values.add(value.strip())


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if parsed >= 0 and parsed == parsed else None


def _weather_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if -100 <= parsed <= 100 and parsed == parsed else None


def _optional_weather_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _compact_text(value: str) -> str:
    return re.sub(r"[\s·•（）()\-—_]+", "", value).lower()


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
