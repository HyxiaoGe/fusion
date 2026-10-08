"""和风天气 API 客户端：Ed25519 JWT 鉴权、城市查询与逐日预报。

只请求固定路径，不跟随重定向，不重试；错误统一转换为 ``QWeatherError`` 的错误码，
响应体与令牌都不进入日志。
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# 和风允许最长 24 小时；服务端取 15 分钟，到期前 2 分钟换新。
_TOKEN_TTL_SECONDS = 900
_TOKEN_REFRESH_MARGIN_SECONDS = 120
# 文档建议 iat 提前 30 秒，容忍时钟误差。
_TOKEN_IAT_SKEW_SECONDS = 30
_MAX_RESPONSE_BYTES = 256 * 1024


class QWeatherError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QWeatherCredentials:
    api_host: str
    key_id: str
    project_id: str
    developer_id: str
    private_key: Ed25519PrivateKey

    @classmethod
    def from_settings(
        cls,
        *,
        api_host: str,
        key_id: str,
        project_id: str,
        developer_id: str,
        private_key: str,
    ) -> QWeatherCredentials | None:
        """配置不全时返回 None（不启用）；配置齐全但私钥无效时抛 ValueError。"""

        values = (api_host, key_id, project_id, developer_id, private_key)
        if not all(value and value.strip() for value in values):
            return None
        host = api_host.strip().lower()
        if "/" in host or ":" in host or not host.endswith(".qweatherapi.com"):
            raise ValueError("和风天气 API Host 无效")
        return cls(
            api_host=host,
            key_id=key_id.strip(),
            project_id=project_id.strip(),
            developer_id=developer_id.strip(),
            private_key=_load_private_key(private_key),
        )


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


class QWeatherClient:
    def __init__(
        self,
        credentials: QWeatherCredentials,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        self.credentials = credentials
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def lookup_city(self, location: str, *, number: int = 10, lang: str = "zh") -> list[QWeatherCity]:
        payload = await self._get(
            "/geo/v2/city/lookup",
            {"location": location, "number": str(number), "lang": lang},
            not_found_is_empty=True,
        )
        cities: list[QWeatherCity] = []
        for raw in payload.get("location") or []:
            city = _parse_city(raw)
            if city is not None:
                cities.append(city)
        return cities

    async def daily_forecast(self, location_id: str) -> list[QWeatherDay]:
        payload = await self._get("/v7/weather/7d", {"location": location_id, "lang": "zh"})
        days: list[QWeatherDay] = []
        for raw in payload.get("daily") or []:
            day = _parse_day(raw)
            if day is not None:
                days.append(day)
        return days

    async def _get(self, path: str, params: dict[str, str], *, not_found_is_empty: bool = False) -> dict[str, Any]:
        token = self._bearer_token()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"https://{self.credentials.api_host}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"},
                )
        except httpx.TimeoutException:
            raise QWeatherError("upstream_timeout") from None
        except httpx.HTTPError:
            raise QWeatherError("upstream_unavailable") from None
        # 城市查询对无法识别的地名会回 400/404（如“鼓浪屿”），按查无结果处理。
        if response.status_code in (400, 404) and not_found_is_empty:
            return {}
        if response.status_code in (401, 403):
            raise QWeatherError("upstream_auth_failed")
        if response.status_code == 429:
            raise QWeatherError("upstream_rate_limited")
        if response.status_code != 200 or len(response.content) > _MAX_RESPONSE_BYTES:
            raise QWeatherError("upstream_unavailable")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise QWeatherError("invalid_response") from None
        if not isinstance(payload, dict):
            raise QWeatherError("invalid_response")
        code = str(payload.get("code", ""))
        if code == "404" and not_found_is_empty:
            return {}
        if code != "200":
            raise QWeatherError("invalid_response")
        return payload

    def _bearer_token(self) -> str:
        # 同步签名、中间无 await，同一事件循环内不会并发重入；客户端跨 run 复用，不持有事件循环绑定的锁。
        now = self.clock()
        if self._token is None or now >= self._token_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
            issued_at = int(now) - _TOKEN_IAT_SKEW_SECONDS
            self._token = sign_qweather_jwt(self.credentials, issued_at=issued_at)
            self._token_expires_at = issued_at + _TOKEN_TTL_SECONDS
        return self._token


def sign_qweather_jwt(credentials: QWeatherCredentials, *, issued_at: int) -> str:
    """按和风要求只放 alg/kid 与 iss/sub/iat/exp，不加 typ 等保留字段。"""

    header = _b64url(json.dumps({"alg": "EdDSA", "kid": credentials.key_id}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": credentials.developer_id,
                "sub": credentials.project_id,
                "iat": issued_at,
                "exp": issued_at + _TOKEN_TTL_SECONDS,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_b64url(credentials.private_key.sign(signing_input))}"


def _load_private_key(value: str) -> Ed25519PrivateKey:
    text = value.strip()
    if "-----BEGIN" not in text:
        body = "".join(text.split())
        text = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n"
    try:
        key = load_pem_private_key(text.encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError("和风天气私钥无效") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("和风天气私钥必须是 Ed25519")
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _text(raw: dict[str, Any], key: str, max_length: int) -> str | None:
    value = raw.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:max_length] if value else None


def _parse_city(raw: Any) -> QWeatherCity | None:
    if not isinstance(raw, dict):
        return None
    location_id = _text(raw, "id", 32)
    name = _text(raw, "name", 80)
    if not location_id or not name:
        return None
    rank_text = _text(raw, "rank", 4)
    return QWeatherCity(
        location_id=location_id,
        name=name,
        adm1=_text(raw, "adm1", 80) or "",
        adm2=_text(raw, "adm2", 80) or "",
        country=_text(raw, "country", 80) or "",
        rank=int(rank_text) if rank_text and rank_text.isdigit() else None,
    )


def _parse_day(raw: Any) -> QWeatherDay | None:
    if not isinstance(raw, dict):
        return None
    required = {key: _text(raw, key, 80) for key in ("fxDate", "textDay", "textNight", "tempMax", "tempMin")}
    if not all(required.values()):
        return None
    return QWeatherDay(
        date=required["fxDate"],
        text_day=required["textDay"],
        text_night=required["textNight"],
        temp_max=required["tempMax"],
        temp_min=required["tempMin"],
        wind_dir_day=_text(raw, "windDirDay", 40),
        wind_dir_night=_text(raw, "windDirNight", 40),
        wind_scale_day=_text(raw, "windScaleDay", 20),
        wind_scale_night=_text(raw, "windScaleNight", 20),
    )
