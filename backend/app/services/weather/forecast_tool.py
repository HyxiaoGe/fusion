"""weather_forecast：经 agent-tools 网关调用天气服务的 get_weather（和风天气）。

挑城市、缓存、整理结果、给模型的说明和失败话术都在服务里（agent-tools services/weather）。
这里只做 Fusion 自己的事：工具在 Fusion 叫 weather_forecast；查当前位置时把页面授权的设备位置放进 _meta；
把结果画成天气卡片；在服务说明后面接 Fusion 的约定（qweather.product_notes）。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date as CalendarDate
from typing import Any

from pydantic import ValidationError

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import (
    StructuredResultAttribution,
    WeatherForecastDay,
    WeatherHourlyPoint,
    WeatherResultsBlock,
)
from app.services.external.agent_tools_mcp import (
    GEOLOCATION_META_KEY,
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
)
from app.services.mcp.product_context import format_product_context, redact_product_text
from app.services.mcp.tool_contract import canonical_json_bytes, validate_tool_arguments
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext
from app.services.weather import WEATHER_FORECAST_TOOL_NAME

WEATHER_SERVICE = "weather"
WEATHER_REMOTE_TOOL = "get_weather"
WEATHER_PROVIDER = "qweather"
CURRENT_LOCATION = "current_location"
_CALL_SCENE = "chat_weather"
# 服务整个调用 20 秒超时，留出网关转发的余量。
_CALL_TIMEOUT_SECONDS = 25.0
_CARD_MAX_DAYS = 4
_CARD_MAX_HOURS = 24
_CITY_LEVEL_LIMITATION = "天气预报按城市或区县提供，不代表具体建筑物"
# 服务的 limitations 是英文代码，卡片按中文显示；requested_date_not_covered 时没有逐日数据，不出卡片。
_DEGRADED_LIMITATIONS = frozenset({"fewer_days", "requested_date_not_covered"})


def build_weather_forecast_definition(spec: GatewayToolSpec) -> dict[str, Any]:
    return build_gateway_tool_definition(
        spec,
        name=WEATHER_FORECAST_TOOL_NAME,
        product_notes=render_runtime_prompt("qweather.product_notes"),
    )


@dataclass(frozen=True)
class WeatherToolBinding:
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


def build_weather_binding(definition: dict[str, Any]) -> WeatherToolBinding:
    return WeatherToolBinding(
        alias=WEATHER_FORECAST_TOOL_NAME,
        remote_tool_name=WEATHER_REMOTE_TOOL,
        provider=WEATHER_PROVIDER,
        tool_label="和风天气预报",
        definition_sha256=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
    )


class WeatherForecastToolHandler(BaseToolHandler):
    # 服务的失败一律不可重试（同样的参数再查也不会好），由模型按服务的说明换地名或换来源。
    supports_automatic_retry = False

    def __init__(
        self,
        *,
        binding: WeatherToolBinding,
        parameters: dict[str, Any],
        call_tool: Any = call_gateway_tool,
        max_llm_context_bytes: int = 12_000,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.binding = binding
        self.parameters = parameters
        self.call_tool = call_tool
        self.max_llm_context_bytes = max_llm_context_bytes
        self.monotonic = monotonic

    @property
    def tool_name(self) -> str:
        return self.binding.alias

    @property
    def sse_event_prefix(self) -> str:
        return "mcp"

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """只按公告的 schema 校验形状；地名、日期、当前位置两处是否对得上由服务判断并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(self.parameters, args)

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        """同一 Run 里同样参数的成功预报直接复用（当前位置在一个 Run 内只取一次）。"""
        if not isinstance(input_params, dict):
            return None
        try:
            return hashlib.sha256(
                canonical_json_bytes({"tool_name": self.tool_name, "arguments": input_params})
            ).hexdigest()
        except (TypeError, ValueError):
            return None

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        started_at = self.monotonic()
        current_location = args.get("location_source") == CURRENT_LOCATION
        outcome: GatewayToolOutcome = await self.call_tool(
            WEATHER_SERVICE,
            WEATHER_REMOTE_TOOL,
            args,
            scene=_CALL_SCENE,
            meta=_geolocation_meta(context) if current_location else None,
            min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
        )
        if outcome.ok and not outcome.model_text:
            outcome = GatewayToolOutcome(ok=False, error_code="invalid_response")
        if not outcome.ok:
            if not outcome.model_text:
                logger.warning("天气服务没有答复: error_code=%s", outcome.error_code)
            data: dict[str, Any] = {
                **self._metadata(),
                "error_code": outcome.error_code or "tool_error",
                "retryable": False,
                "model_text": outcome.model_text,
            }
            candidates = outcome.error.get("candidates")
            if isinstance(candidates, list) and candidates:
                data["candidates"] = candidates
            return ToolResult(
                status="failed",
                duration_ms=self._duration_ms(started_at),
                data=data,
                error_message="天气查询失败",
            )
        limitations = outcome.data.get("limitations")
        degraded = isinstance(limitations, list) and bool(_DEGRADED_LIMITATIONS.intersection(limitations))
        location = args.get("location")
        query = "当前位置" if current_location else redact_product_text(str(location or ""))[:120]
        return ToolResult(
            status="degraded" if degraded else "success",
            duration_ms=self._duration_ms(started_at),
            data={**self._metadata(), "query": query, "result": outcome.data, "model_text": outcome.model_text},
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> WeatherResultsBlock | None:
        if result.status not in {"success", "degraded"}:
            return None
        product_result = result.data.get("result")
        if not isinstance(product_result, dict):
            return None
        try:
            days = [_forecast_day(day) for day in (product_result.get("forecast_days") or [])[:_CARD_MAX_DAYS]]
            if not days:
                return None
            return WeatherResultsBlock(
                type="weather_results",
                id=block_id,
                schema_version=1,
                provider=WEATHER_PROVIDER,
                attribution=StructuredResultAttribution(label="和风天气"),
                status=result.status,
                query=result.data["query"],
                resolved_location=str(product_result["resolved_location"])[:120],
                requested_date=product_result.get("requested_date"),
                day_count=len(days),
                forecast_days=days,
                fetched_at=product_result["fetched_at"],
                hourly=_hourly_points(product_result.get("hourly")),
                limitations=_card_limitations(product_result.get("limitations"), day_count=len(days)),
                tool_call_log_id=log_id,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return None

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        model_text = data.get("model_text")
        if not isinstance(model_text, str):
            model_text = ""
        if result.status not in {"success", "degraded"}:
            # 服务答了就用服务给模型的说明（换候选、问城市等）；网关或网络出错时用 Fusion 的说明。
            text = model_text or render_runtime_prompt("qweather.weather_unavailable")
            return text.encode()[: self.max_llm_context_bytes].decode(errors="ignore")
        return format_product_context(
            tool_name=self.tool_name,
            payload_text=model_text,
            max_bytes=self.max_llm_context_bytes,
            usage_contract="",
        )

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        data = result.data or {}
        output: dict[str, Any] = {**self._metadata(), "status": result.status}
        if result.status in {"success", "degraded"} and isinstance(data.get("result"), dict):
            output["resolved_location"] = data["result"].get("resolved_location")
            output["day_count"] = data["result"].get("day_count")
            output["limitations"] = data["result"].get("limitations")
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

    def _metadata(self) -> dict[str, Any]:
        return {
            "provider": self.binding.provider,
            "remote_tool_name": self.binding.remote_tool_name,
            "definition_sha256": self.binding.definition_sha256,
        }

    def _duration_ms(self, started_at: float) -> int:
        return max(0, int((self.monotonic() - started_at) * 1_000))


def _geolocation_meta(context: ToolRuntimeContext | None) -> dict[str, Any] | None:
    """没有设备位置时不带 _meta，服务回 location_unavailable，让模型问用户在哪个城市。"""
    geolocation = getattr(context, "geolocation", None)
    if geolocation is None:
        return None
    return {
        GEOLOCATION_META_KEY: {
            "longitude": geolocation.longitude,
            "latitude": geolocation.latitude,
            "accuracy_m": geolocation.accuracy_m,
        }
    }


def _forecast_day(day: dict[str, Any]) -> WeatherForecastDay:
    forecast_date = CalendarDate.fromisoformat(day["date"])
    return WeatherForecastDay(
        date=forecast_date,
        weekday=forecast_date.isoweekday(),
        day_weather=day["day_weather"],
        night_weather=day["night_weather"],
        high_c=day["high_c"],
        low_c=day["low_c"],
        day_wind_direction=day.get("day_wind_direction"),
        night_wind_direction=day.get("night_wind_direction"),
        day_wind_power=day.get("day_wind_power"),
        night_wind_power=day.get("night_wind_power"),
    )


def _hourly_points(hourly: Any) -> list[WeatherHourlyPoint]:
    """逐小时只给卡片画趋势图；个别点不合规就跳过，不影响整张卡片。"""
    if not isinstance(hourly, list):
        return []
    points: list[WeatherHourlyPoint] = []
    for point in hourly:
        if len(points) == _CARD_MAX_HOURS:
            break
        if not isinstance(point, dict):
            continue
        try:
            parsed = WeatherHourlyPoint.model_validate(
                {key: point.get(key) for key in ("time", "temp_c", "weather", "pop", "precip_mm")}
            )
        except ValidationError:
            continue
        if points and parsed.time <= points[-1].time:
            continue
        points.append(parsed)
    return points


def _card_limitations(codes: Any, *, day_count: int) -> list[str]:
    limitations = [_CITY_LEVEL_LIMITATION]
    codes = codes if isinstance(codes, list) else []
    if "fewer_days" in codes:
        limitations.append(f"仅返回 {day_count} 天有效预报")
    if "hourly_unavailable" in codes:
        limitations.append("逐小时预报暂时取不到")
    return limitations
