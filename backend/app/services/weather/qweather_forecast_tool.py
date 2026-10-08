"""和风天气提供的 weather_forecast 产品工具。

工具定义、参数校验、结果块与模型上下文契约与原高德天气一致，只换数据来源。
城市查询是模糊匹配（随意输入也会返回城市），因此候选必须满足：
- 城市名出现在用户给的地名里；
- 国外且 rank 大于 80 的低重要度地点不参与；
- 多个候选无法用地名中的上级行政区区分时，不按排名猜，把候选交给模型判断或问用户。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date as CalendarDate
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import StructuredResultAttribution, WeatherForecastDay, WeatherResultsBlock
from app.services.mcp.amap_product_tools import (
    WEATHER_FORECAST_DEFINITION,
    WEATHER_RESULT_USAGE_CONTRACT,
    InvalidWeatherArguments,
    bound_product_result,
    format_product_context,
    redact_product_text,
    validate_weather_arguments,
)
from app.services.mcp.tool_contract import canonical_json_bytes
from app.services.tool_handlers.base import BaseToolHandler, ToolResult
from app.services.weather.qweather_client import QWeatherCity, QWeatherClient, QWeatherDay, QWeatherError

QWEATHER_PROVIDER = "qweather"
WEATHER_FORECAST_TOOL_NAME = "weather_forecast"
FORECAST_DAYS = 4
MAX_CANDIDATES_SHOWN = 5
# 国外地点 rank 大于该值时视为低重要度，不参与匹配（实测乱写的地名与同名小地方都在 83–85）。
FOREIGN_MAX_RANK = 80
_DOMESTIC_COUNTRY = "中国"
_TOOL_TIMEOUT_SECONDS = 20.0
_CACHE_TTL_SECONDS = 30 * 60
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LABEL_SEPARATOR = "·"
# 只用于从上级行政区名里取出用户常写的简称（浙江省→浙江），不判断语义。
_ADMIN_SUFFIXES = ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市")


@dataclass(frozen=True)
class QWeatherToolBinding:
    alias: str
    remote_tool_name: str
    provider: str
    tool_label: str
    definition_sha256: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "remote_tool_name": self.remote_tool_name,
            "provider": self.provider,
            "tool_label": self.tool_label,
            "definition_sha256": self.definition_sha256,
        }


def build_qweather_binding() -> QWeatherToolBinding:
    return QWeatherToolBinding(
        alias=WEATHER_FORECAST_TOOL_NAME,
        remote_tool_name=f"adapter:{WEATHER_FORECAST_TOOL_NAME}",
        provider=QWEATHER_PROVIDER,
        tool_label="和风天气预报",
        definition_sha256=hashlib.sha256(canonical_json_bytes(WEATHER_FORECAST_DEFINITION)).hexdigest(),
    )


class _ForecastCache:
    """进程内短缓存：同一城市 30 分钟内复用预报，减少重复调用。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, list[QWeatherDay]]] = {}

    def get(self, location_id: str, now: float) -> list[QWeatherDay] | None:
        entry = self._entries.get(location_id)
        if entry is None or now - entry[0] > _CACHE_TTL_SECONDS:
            return None
        return entry[1]

    def set(self, location_id: str, days: list[QWeatherDay], now: float) -> None:
        if len(self._entries) > 512:
            self._entries.clear()
        self._entries[location_id] = (now, days)


_FORECAST_CACHE = _ForecastCache()


class _Unresolved(Exception):
    def __init__(self, code: str, candidates: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.candidates = candidates or []


class QWeatherForecastToolHandler(BaseToolHandler):
    supports_automatic_retry = False

    def __init__(
        self,
        *,
        binding: QWeatherToolBinding,
        client: QWeatherClient,
        max_llm_context_bytes: int = 12_000,
        cache: _ForecastCache | None = None,
        now: Any = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.binding = binding
        self.client = client
        self.max_llm_context_bytes = max_llm_context_bytes
        self.cache = cache or _FORECAST_CACHE
        self.now = now or (lambda: datetime.now(_SHANGHAI_TZ))
        self.monotonic = monotonic

    @property
    def tool_name(self) -> str:
        return self.binding.alias

    @property
    def sse_event_prefix(self) -> str:
        return "mcp"

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        try:
            validate_weather_arguments(args)
        except InvalidWeatherArguments:
            return [{"field": "request", "code": "invalid_arguments"}]
        return []

    async def execute(self, args: dict) -> ToolResult:
        return await self._execute(args, runtime_context=None)

    async def execute_with_runtime_context(self, args: dict, runtime_context: Any) -> ToolResult:
        return await self._execute(args, runtime_context=runtime_context)

    async def _execute(self, args: dict, *, runtime_context: Any) -> ToolResult:
        started_at = self.monotonic()
        try:
            normalized = validate_weather_arguments(args)
        except InvalidWeatherArguments:
            return self._failed(started_at, "invalid_arguments")
        try:
            async with asyncio.timeout(_TOOL_TIMEOUT_SECONDS):
                city = await self._resolve_city(normalized, runtime_context)
                days = await self._forecast(city.location_id)
        except _Unresolved as unresolved:
            return self._failed(started_at, unresolved.code, candidates=unresolved.candidates)
        except QWeatherError as error:
            logger.warning("和风天气调用失败: error_code=%s", error.code)
            return self._failed(started_at, error.code)
        except TimeoutError:
            return self._failed(started_at, "call_timeout")

        forecast_days = _build_forecast_days(days, today=self.now().astimezone(_SHANGHAI_TZ).date())
        if not forecast_days:
            return self._failed(started_at, "invalid_response")
        limitations = ["天气预报按城市或区县提供，不代表具体建筑物"]
        if len(forecast_days) < FORECAST_DAYS:
            limitations.append(f"仅返回 {len(forecast_days)} 天有效预报")
        product_result: dict[str, Any] = {
            "query": redact_product_text(normalized["location"])[:120],
            "resolved_location": city_label(city),
            "day_count": len(forecast_days),
            "forecast_days": [day.model_dump(mode="json") for day in forecast_days],
            "fetched_at": self.now().isoformat(),
            "limitations": limitations,
        }
        if normalized.get("requested_date"):
            product_result["requested_date"] = normalized["requested_date"]
        return ToolResult(
            status="success" if len(forecast_days) == FORECAST_DAYS else "degraded",
            duration_ms=_duration_ms(self.monotonic, started_at),
            data={**self._metadata(), "result": bound_product_result(product_result)},
        )

    async def _resolve_city(self, normalized: dict[str, Any], runtime_context: Any) -> QWeatherCity:
        if normalized["location_source"] == "current_location":
            geolocation = getattr(runtime_context, "geolocation", None)
            latitude = getattr(geolocation, "latitude", None)
            longitude = getattr(geolocation, "longitude", None)
            if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
                raise _Unresolved("location_context_unavailable")
            # 和风接受最多两位小数的“经度,纬度”；城市级天气不受这一精度影响。
            cities = await self.client.lookup_city(f"{longitude:.2f},{latitude:.2f}", number=1)
            if not cities:
                raise _Unresolved("location_not_found")
            return cities[0]
        query = normalized["location"]
        # 模型用候选标签（如“江西省·南昌·西湖”）重试时只按末段地名查询，再按完整标签匹配。
        lookup_query = query.rsplit(LABEL_SEPARATOR, 1)[-1].strip() or query
        # 纯英文地名按英文名匹配（lang=zh 时返回的是中文名，"Paris" 匹配不到“巴黎”）。
        lang = "en" if lookup_query.isascii() else "zh"
        return select_city(query, await self.client.lookup_city(lookup_query, lang=lang))

    async def _forecast(self, location_id: str) -> list[QWeatherDay]:
        now = self.monotonic()
        cached = self.cache.get(location_id, now)
        if cached is not None:
            return cached
        days = await self.client.daily_forecast(location_id)
        if days:
            self.cache.set(location_id, days, now)
        return days

    def _metadata(self) -> dict[str, Any]:
        return {
            "provider": self.binding.provider,
            "remote_tool_name": self.binding.remote_tool_name,
            "definition_sha256": self.binding.definition_sha256,
        }

    def _failed(self, started_at: float, error_code: str, *, candidates: list[str] | None = None) -> ToolResult:
        data: dict[str, Any] = {**self._metadata(), "error_code": error_code, "retryable": False}
        if candidates:
            data["candidates"] = candidates
        return ToolResult(
            status="failed",
            duration_ms=_duration_ms(self.monotonic, started_at),
            data=data,
            error_message="天气查询失败",
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> WeatherResultsBlock | None:
        if result.status not in {"success", "degraded"}:
            return None
        product_result = result.data.get("result")
        if not isinstance(product_result, dict):
            return None
        try:
            days = [WeatherForecastDay.model_validate(day) for day in product_result["forecast_days"][:FORECAST_DAYS]]
            return WeatherResultsBlock(
                type="weather_results",
                id=block_id,
                schema_version=1,
                provider=QWEATHER_PROVIDER,
                attribution=StructuredResultAttribution(label="和风天气"),
                status=result.status,
                query=product_result["query"],
                resolved_location=product_result["resolved_location"],
                requested_date=product_result.get("requested_date"),
                day_count=len(days),
                forecast_days=days,
                fetched_at=product_result["fetched_at"],
                limitations=product_result.get("limitations") or [],
                tool_call_log_id=log_id,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return None

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status not in {"success", "degraded"} or "result" not in data:
            error_code = data.get("error_code")
            if error_code == "ambiguous_location":
                return render_runtime_prompt(
                    "qweather.ambiguous_location",
                    candidates=json.dumps(data.get("candidates") or [], ensure_ascii=False),
                )
            if error_code == "location_not_found":
                return render_runtime_prompt("qweather.location_not_found")
            return render_runtime_prompt("amap.weather_unavailable")
        return format_product_context(
            tool_name=self.tool_name,
            payload_text=json.dumps(data["result"], ensure_ascii=False, sort_keys=True),
            max_bytes=self.max_llm_context_bytes,
            usage_contract=WEATHER_RESULT_USAGE_CONTRACT,
        )

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        data = result.data or {}
        output: dict[str, Any] = {**self._metadata(), "status": result.status}
        if result.status in {"success", "degraded"} and isinstance(data.get("result"), dict):
            output["resolved_location"] = data["result"].get("resolved_location")
            output["day_count"] = data["result"].get("day_count")
        else:
            output["error_code"] = data.get("error_code")
            if data.get("candidates"):
                output["candidate_count"] = len(data["candidates"])
        return output

    def _build_result_summary(self, result: ToolResult) -> dict:
        data = result.data or {}
        summary: dict[str, Any] = {"kind": "weather", "truncated": False}
        if result.status in {"success", "degraded"} and isinstance(data.get("result"), dict):
            summary["resolved_location"] = data["result"].get("resolved_location")
        else:
            summary["error_code"] = data.get("error_code")
        return summary


def select_city(query: str, cities: list[QWeatherCity]) -> QWeatherCity:
    """按地名文本结构从模糊候选中选城市；无法唯一确定时抛出未解析。"""

    candidates = [city for city in cities if not _is_low_rank_foreign(city)]
    for city in candidates:
        if city_label(city) == query:
            return city
    matched = [city for city in candidates if city.name in query]
    if not matched:
        raise _Unresolved("location_not_found")
    hinted = [city for city in matched if _admin_hint_in_query(city, query)]
    if hinted:
        matched = hinted
    # 地名一般由大到小书写：取名字在地名中结束得最靠后的，其次取名字更长的（更具体）。
    best_end = max(query.rfind(city.name) + len(city.name) for city in matched)
    matched = [city for city in matched if query.rfind(city.name) + len(city.name) == best_end]
    longest = max(len(city.name) for city in matched)
    matched = [city for city in matched if len(city.name) == longest]
    unique = list({city.location_id: city for city in matched}.values())
    if len(unique) == 1:
        return unique[0]
    labels = list(dict.fromkeys(city_label(city) for city in unique))
    if len(labels) == 1:
        # 标签完全相同的重复条目无法也无需让用户区分，按服务返回顺序取第一个。
        return unique[0]
    raise _Unresolved("ambiguous_location", labels[:MAX_CANDIDATES_SHOWN])


def city_label(city: QWeatherCity) -> str:
    parts = [city.adm1, city.adm2, city.name]
    if city.country and city.country != _DOMESTIC_COUNTRY:
        parts.insert(0, city.country)
    deduped: list[str] = []
    for part in parts:
        if part and (not deduped or deduped[-1] != part):
            deduped.append(part)
    return LABEL_SEPARATOR.join(deduped)[:120]


def _is_low_rank_foreign(city: QWeatherCity) -> bool:
    return (
        bool(city.country)
        and city.country != _DOMESTIC_COUNTRY
        and city.rank is not None
        and city.rank > FOREIGN_MAX_RANK
    )


def _admin_hint_in_query(city: QWeatherCity, query: str) -> bool:
    for part in (city.adm1, city.adm2, city.country):
        short = _strip_admin_suffix(part)
        if len(short) >= 2 and short != city.name and short in query:
            return True
    return False


def _strip_admin_suffix(value: str) -> str:
    for suffix in _ADMIN_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _build_forecast_days(days: list[QWeatherDay], *, today: CalendarDate) -> list[WeatherForecastDay]:
    built: list[WeatherForecastDay] = []
    seen: set[CalendarDate] = set()
    for day in days:
        try:
            forecast_date = CalendarDate.fromisoformat(day.date)
            # 海外城市按当地日期返回，允许比北京时间早一天。
            if forecast_date < CalendarDate.fromordinal(today.toordinal() - 1) or forecast_date in seen:
                continue
            built.append(
                WeatherForecastDay(
                    date=forecast_date,
                    weekday=forecast_date.isoweekday(),
                    day_weather=day.text_day,
                    night_weather=day.text_night,
                    high_c=float(day.temp_max),
                    low_c=float(day.temp_min),
                    day_wind_direction=day.wind_dir_day,
                    night_wind_direction=day.wind_dir_night,
                    day_wind_power=_wind_power(day.wind_scale_day),
                    night_wind_power=_wind_power(day.wind_scale_night),
                )
            )
            seen.add(forecast_date)
        except (ValueError, ValidationError):
            continue
        if len(built) == FORECAST_DAYS:
            break
    return built


def _wind_power(scale: str | None) -> str | None:
    return f"{scale}级" if scale else None


def _duration_ms(monotonic: Any, started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1_000))
