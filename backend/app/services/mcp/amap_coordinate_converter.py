"""经 agent-tools 高德代理的 maps_coordinate_convert 工具，把设备 WGS84 坐标转换为 GCJ02。

高德密钥只在 agent-tools 的 amap 代理里，Fusion 不再直连高德 Web 服务；坐标转换和其他高德
调用走同一条 MCP 连接，共用 run 预算、授权复核与熔断。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any, Protocol

from app.services.agent.context_broker import Geolocation

COORDINATE_CONVERT_TOOL = "maps_coordinate_convert"
_SAFE_ERROR_MESSAGE = "高德坐标转换不可用"
_COORDINATE_PATTERN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*$")


class AmapCoordinateConversionError(Exception):
    def __init__(self) -> None:
        super().__init__(_SAFE_ERROR_MESSAGE)


class _RemoteCaller(Protocol):
    async def call(
        self,
        remote_tool_name: str,
        expected_definition_sha256: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]: ...


def build_remote_coordinate_converter(
    remote_executor: _RemoteCaller,
    dependency_hashes: Mapping[str, str],
) -> Callable[[Geolocation], Awaitable[str]]:
    """返回按本 run 授权快照调用 maps_coordinate_convert 的转换函数；失败只抛固定文案。"""

    async def convert(location: Geolocation) -> str:
        expected_hash = dependency_hashes.get(COORDINATE_CONVERT_TOOL)
        if not expected_hash:
            raise AmapCoordinateConversionError
        arguments = {
            "locations": f"{location.longitude:.6f},{location.latitude:.6f}",
            "coordsys": "gps",
        }
        try:
            payload = await remote_executor.call(COORDINATE_CONVERT_TOOL, expected_hash, arguments)
        except Exception:
            raise AmapCoordinateConversionError from None
        return parse_converted_coordinate(payload)

    return convert


def parse_converted_coordinate(payload: Any) -> str:
    """从工具结果里取出 `lng,lat`；取不到或超出经纬度范围都按转换失败处理。"""

    for candidate in _iter_text_candidates(payload):
        match = _COORDINATE_PATTERN.match(candidate)
        if match is None:
            continue
        longitude = float(match.group(1))
        latitude = float(match.group(2))
        if -180 <= longitude <= 180 and -90 <= latitude <= 90:
            return f"{longitude:.6f},{latitude:.6f}"
    raise AmapCoordinateConversionError


def _iter_text_candidates(payload: Any) -> Iterator[str]:
    if not isinstance(payload, dict) or payload.get("isError"):
        return
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                yield item["text"]
    for key in ("structuredContent", "structured_content"):
        structured = payload.get(key)
        if isinstance(structured, dict) and isinstance(structured.get("result"), str):
            yield structured["result"]
