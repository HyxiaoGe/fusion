"""和风天气客户端：经 agent-tools 网关的 weather 代理取城市与预报。

和风凭证与 JWT 签名都在代理侧，这里只带 Fusion 的产品服务密钥；只请求代理的三个固定路径，不跟随重定向，
不重试；错误统一转换为 ``QWeatherError`` 的错误码，响应体不进入日志。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.external.agent_tools import agent_tools_headers, agent_tools_url

WEATHER_SERVICE = "weather"
WEATHER_SCENE = "weather_forecast"
_MAX_RESPONSE_BYTES = 256 * 1024
# 代理返回的结构化错误码原样保留，便于区分“代理未配置凭证”和“和风自身故障”；其他一律视为代理不可用。
_PROXY_ERROR_CODES = frozenset(
    {
        "weather_not_configured",
        "upstream_timeout",
        "upstream_unavailable",
        "upstream_auth_failed",
        "upstream_rate_limited",
        "invalid_response",
    }
)


class QWeatherError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QWeatherCity:
    location_id: str
    name: str
    adm1: str
    adm2: str
    country: str
    rank: int | None


@dataclass(frozen=True)
class QWeatherDay:
    date: str
    text_day: str
    text_night: str
    temp_max: str
    temp_min: str
    wind_dir_day: str | None
    wind_dir_night: str | None
    wind_scale_day: str | None
    wind_scale_night: str | None


@dataclass(frozen=True)
class QWeatherHour:
    time: str
    temp: str
    text: str
    pop: str | None
    precip: str | None


class QWeatherClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def lookup_city(self, location: str, *, number: int = 10, lang: str = "zh") -> list[QWeatherCity]:
        payload = await self._get("/v1/cities", {"location": location, "number": str(number), "lang": lang})
        return _parse_all(payload.get("cities"), _parse_city)

    async def daily_forecast(self, location_id: str) -> list[QWeatherDay]:
        payload = await self._get("/v1/forecast/daily", {"location_id": location_id})
        return _parse_all(payload.get("daily"), _parse_day)

    async def hourly_forecast(self, location_id: str) -> list[QWeatherHour]:
        payload = await self._get("/v1/forecast/hourly", {"location_id": location_id})
        return _parse_all(payload.get("hourly"), _parse_hour)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    agent_tools_url(WEATHER_SERVICE, path),
                    params=params,
                    headers=agent_tools_headers(WEATHER_SCENE),
                )
        except httpx.TimeoutException:
            raise QWeatherError("upstream_timeout") from None
        except httpx.HTTPError:
            raise QWeatherError("upstream_unavailable") from None
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise QWeatherError("invalid_response")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        if response.status_code != 200:
            raise QWeatherError(_error_code(response.status_code, payload))
        if not isinstance(payload, dict):
            raise QWeatherError("invalid_response")
        return payload


def _error_code(status_code: int, payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code in _PROXY_ERROR_CODES:
        return code
    # 网关认不出产品密钥或未放行 weather 服务：同样当鉴权失败，和凭证失效一样需要人工处理。
    if status_code in (401, 403):
        return "upstream_auth_failed"
    return "upstream_unavailable"


def _parse_all(raw_items: Any, parse: Any) -> list[Any]:
    items: list[Any] = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        item = parse(raw)
        if item is not None:
            items.append(item)
    return items


def _text(raw: dict[str, Any], key: str, max_length: int) -> str | None:
    value = raw.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:max_length] if value else None


def _parse_city(raw: Any) -> QWeatherCity | None:
    if not isinstance(raw, dict):
        return None
    location_id = _text(raw, "location_id", 32)
    name = _text(raw, "name", 80)
    if not location_id or not name:
        return None
    rank = raw.get("rank")
    return QWeatherCity(
        location_id=location_id,
        name=name,
        adm1=_text(raw, "adm1", 80) or "",
        adm2=_text(raw, "adm2", 80) or "",
        country=_text(raw, "country", 80) or "",
        rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
    )


def _parse_day(raw: Any) -> QWeatherDay | None:
    if not isinstance(raw, dict):
        return None
    required = {key: _text(raw, key, 80) for key in ("date", "text_day", "text_night", "temp_max", "temp_min")}
    if not all(required.values()):
        return None
    return QWeatherDay(
        date=required["date"],
        text_day=required["text_day"],
        text_night=required["text_night"],
        temp_max=required["temp_max"],
        temp_min=required["temp_min"],
        wind_dir_day=_text(raw, "wind_dir_day", 40),
        wind_dir_night=_text(raw, "wind_dir_night", 40),
        wind_scale_day=_text(raw, "wind_scale_day", 20),
        wind_scale_night=_text(raw, "wind_scale_night", 20),
    )


def _parse_hour(raw: Any) -> QWeatherHour | None:
    if not isinstance(raw, dict):
        return None
    time_text = _text(raw, "time", 40)
    temp = _text(raw, "temp", 10)
    text = _text(raw, "text", 80)
    if not time_text or not temp or not text:
        return None
    return QWeatherHour(
        time=time_text,
        temp=temp,
        text=text,
        pop=_text(raw, "pop", 4),
        precip=_text(raw, "precip", 10),
    )
