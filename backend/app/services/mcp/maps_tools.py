"""local_place_search / route_compare：经 agent-tools 网关调用地图服务（maps）的 place_search、route_compare。

地名解析与重名候选、先高德后腾讯、当前位置换算、结果整理、给模型的说明和失败话术都在服务里
（agent-tools services/maps）。这里只做 Fusion 自己的事：
- 说明用服务原文，后面接 Fusion 的约定（maps.place_notes / maps.route_notes）；服务目录里没有就不公告。
- 当前位置：页面授权的设备位置放进 _meta，模型参数里没有坐标。
- 航班/高铁接驳的站点城市检查：要用本次 Run 里已出的班次卡片，服务看不到，留在这里。
- 一个 Run 里两个工具合计的调用上限；服务回 quota_exhausted 时按 reset_at 暂停公告。
- 结果画成地点 / 路线卡片；服务的 limitations 是英文代码，卡片按中文显示。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import (
    PlacePhoto,
    PlaceResult,
    PlaceResultsBlock,
    RouteEndpoint,
    RouteOption,
    RouteResultsBlock,
    StructuredResultAction,
    StructuredResultAttribution,
    TransitAlternative,
    TransitLeg,
)
from app.services.external.agent_tools_mcp import (
    GEOLOCATION_META_KEY,
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
)
from app.services.mcp.maps_quota import hide_maps_tools_until
from app.services.mcp.product_context import format_product_context
from app.services.mcp.tool_contract import canonical_json_bytes, validate_tool_arguments
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

MAPS_SERVICE = "maps"
LOCAL_PLACE_SEARCH = "local_place_search"
ROUTE_COMPARE = "route_compare"
MAPS_TOOL_NAMES = frozenset({LOCAL_PLACE_SEARCH, ROUTE_COMPARE})
# Fusion 的工具名 → 服务的工具名；地点搜索在 Fusion 一直叫 local_place_search（会话历史、计费、轨迹都按这个名字）。
MAPS_REMOTE_TOOLS = {LOCAL_PLACE_SEARCH: "place_search", ROUTE_COMPARE: "route_compare"}
CURRENT_LOCATION = "current_location"
# 一个 Run 里两个地图工具合计的调用次数；原来按高德子调用算 64 次，约合 16 次工具调用。
MAPS_MAX_CALLS_PER_RUN = 16
# 服务整次调用 25 秒，网关给 maps 40 秒。
_CALL_TIMEOUT_SECONDS = 30.0
_MAX_CONTEXT_BYTES = 12_000
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOOL_LABELS = {LOCAL_PLACE_SEARCH: "地点搜索", ROUTE_COMPARE: "路线对比"}
_PROVIDER_LABELS = {"amap": "高德地图", "tmap": "腾讯地图"}
_ROUTE_MODES = ("driving", "transit", "walking", "bicycling")
_TRANSIT_TYPES = frozenset({"subway", "bus", "mixed", "public_transit"})
_LEG_KINDS = frozenset({"walking", "subway", "bus", "other"})
_PLACE_LIMITATION = "不包含实时排队或空位信息"
_ROUTE_LIMITATION = "路线时间和距离仅代表本次查询结果"
# 服务 limitations 代码 → 卡片上的中文；没列的代码不显示。
_LIMITATION_TEXTS = {
    "first_term_only": "本次只按第一个关键词搜索，其他关键词未覆盖",
    "departure_time_not_applied": "路线按查询时刻计算，未按指定的出发时间",
}
_CARD_LIMITATIONS_MAX = 8


class MapsRunControls:
    """单次 Agent run 内两个地图工具共享的调用预算。"""

    def __init__(self, *, max_calls: int = MAPS_MAX_CALLS_PER_RUN) -> None:
        if max_calls < 1:
            raise ValueError("地图工具预算配置无效")
        self._max_calls = max_calls
        self._used = 0
        self._lock = asyncio.Lock()

    async def try_consume(self) -> bool:
        async with self._lock:
            if self._used >= self._max_calls:
                return False
            self._used += 1
            return True

    async def is_exhausted(self) -> bool:
        async with self._lock:
            return self._used >= self._max_calls


@dataclass(frozen=True)
class MapsToolBinding:
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


def build_maps_definition(spec: GatewayToolSpec, *, name: str) -> dict[str, Any]:
    notes = "maps.place_notes" if name == LOCAL_PLACE_SEARCH else "maps.route_notes"
    return build_gateway_tool_definition(spec, name=name, product_notes=render_runtime_prompt(notes))


def build_maps_binding(definition: dict[str, Any]) -> MapsToolBinding:
    alias = definition["function"]["name"]
    return MapsToolBinding(
        alias=alias,
        remote_tool_name=MAPS_REMOTE_TOOLS[alias],
        provider=MAPS_SERVICE,
        tool_label=_TOOL_LABELS[alias],
        definition_sha256=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
    )


class MapsToolHandler(BaseToolHandler):
    # 服务的失败只有重名可以再查（模型带候选号），其余同样的参数再查也不会好；不自动重试。
    supports_automatic_retry = False

    def __init__(
        self,
        *,
        binding: MapsToolBinding,
        parameters: dict[str, Any],
        controls: MapsRunControls,
        call_tool: Any = call_gateway_tool,
        hide_tools_until: Any = hide_maps_tools_until,
        max_llm_context_bytes: int = _MAX_CONTEXT_BYTES,
    ) -> None:
        self.binding = binding
        self.parameters = parameters
        self.controls = controls
        self.call_tool = call_tool
        self.hide_tools_until = hide_tools_until
        self.max_llm_context_bytes = max_llm_context_bytes

    @property
    def tool_name(self) -> str:
        return self.binding.alias

    @property
    def sse_event_prefix(self) -> str:
        return "mcp"

    async def is_run_budget_exhausted(self) -> bool:
        return await self.controls.is_exhausted()

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """只按公告的 schema 校验形状；地名、编号、来源和地名是否对得上由服务判断并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(self.parameters, args)

    def check_runtime_arguments(self, args: dict, context: ToolRuntimeContext | None) -> dict[str, Any] | None:
        """班次站点缺少城市或与班次结果的城市不一致时不触网，把班次结果里的城市作为提示交还模型。"""

        if self.tool_name != ROUTE_COMPARE:
            return None
        route_city_hints = getattr(context, "route_city_hints", None)
        if not isinstance(route_city_hints, dict):
            return None
        validation_errors: list[str] = []
        hints: dict[str, list[str]] = {}
        mismatch_detected = False
        for endpoint_field in ("origin", "destination"):
            if args.get(f"{endpoint_field}_source") != "named" or args.get(f"{endpoint_field}_place_id"):
                continue
            city_field = f"{endpoint_field}_city"
            label = args.get(endpoint_field)
            if not isinstance(label, str):
                continue
            candidates = route_city_hints.get(label.strip())
            if (
                not isinstance(candidates, tuple)
                or len(candidates) != 1
                or not isinstance(candidates[0], str)
                or not candidates[0].strip()
            ):
                continue
            provided_city = args.get(city_field)
            if isinstance(provided_city, str) and provided_city.strip():
                if _city_matches(provided_city, candidates[0]):
                    continue
                mismatch_detected = True
                validation_errors.append(f"{city_field}:mismatch")
            else:
                validation_errors.append(f"{city_field}:required")
            hints[city_field] = [candidates[0]]
        if not validation_errors:
            return None
        return {
            "error_code": "route_city_mismatch" if mismatch_detected else "route_city_required",
            "validation_errors": validation_errors,
            "hints": hints,
        }

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        """相同地图查询成功后可在当前 Agent run 内直接复用（当前位置在一个 Run 内只取一次）。

        只做服务也认为等价的归一：文字去首尾空白、值为 null 的可选参数当作没传；默认值由服务决定，这里不补。
        """
        if not isinstance(input_params, dict):
            return None
        arguments = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in input_params.items()
            if value is not None
        }
        try:
            payload = canonical_json_bytes({"tool_name": self.tool_name, "arguments": arguments})
        except (TypeError, ValueError):
            return None
        return hashlib.sha256(payload).hexdigest()

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        started_at = time.monotonic()
        if not await self.controls.try_consume():
            return self._failed_result(started_at, "server_run_budget_exhausted")
        outcome: GatewayToolOutcome = await self.call_tool(
            MAPS_SERVICE,
            self.binding.remote_tool_name,
            args,
            scene=self.tool_name,
            meta=_geolocation_meta(context) if _uses_current_location(args) else None,
            min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
        )
        if outcome.ok and not outcome.model_text:
            outcome = GatewayToolOutcome(ok=False, error_code="invalid_response")
        if not outcome.ok:
            error_code = outcome.error_code or "tool_error"
            if not outcome.model_text:
                logger.warning("地图服务没有答复: tool=%s error_code=%s", self.tool_name, error_code)
            if error_code == "quota_exhausted":
                await self.hide_tools_until(outcome.error.get("reset_at"))
            return self._failed_result(
                started_at,
                error_code,
                model_text=outcome.model_text,
                error_details=_ambiguity_details(outcome.error) if error_code == "ambiguous_location" else None,
            )
        return ToolResult(
            status="degraded" if outcome.data.get("status") == "degraded" else "success",
            duration_ms=_duration_ms(started_at),
            data={
                "remote_tool_name": self.binding.remote_tool_name,
                "result": outcome.data,
                "model_text": outcome.model_text,
            },
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        if result.status not in {"success", "degraded"}:
            return None
        product_result = result.data.get("result")
        if not isinstance(product_result, dict):
            return None
        try:
            if self.tool_name == LOCAL_PLACE_SEARCH:
                return _place_block(product_result, status=result.status, block_id=block_id, log_id=log_id)
            return _route_block(product_result, status=result.status, block_id=block_id, log_id=log_id)
        except (KeyError, TypeError, ValueError, ValidationError):
            logger.warning("地图结果画不成卡片: tool=%s", self.tool_name)
            return None

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        model_text = result.data.get("model_text")
        model_text = model_text if isinstance(model_text, str) else ""
        if result.status not in {"success", "degraded"}:
            # 服务答了就用服务给模型的说明（带候选号再查、问城市、改用地名等）；网关或网络出错时用 Fusion 的说明。
            text = model_text or render_runtime_prompt("maps.unavailable")
            return text.encode()[: self.max_llm_context_bytes].decode(errors="ignore")
        return format_product_context(
            tool_name=self.tool_name,
            payload_text=model_text,
            max_bytes=self.max_llm_context_bytes,
        )

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        return {
            "tool_name": self.tool_name,
            "argument_count": len(input_params) if isinstance(input_params, dict) else 0,
        }

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        output: dict[str, Any] = {
            "tool_name": self.tool_name,
            "remote_tool_name": self.binding.remote_tool_name,
            "status": result.status,
        }
        product_result = result.data.get("result")
        if isinstance(product_result, dict):
            output["providers"] = _providers(product_result)
            if self.tool_name == LOCAL_PLACE_SEARCH:
                output["result_count"] = len(product_result.get("places") or [])
            else:
                output["mode_count"] = len(product_result.get("routes") or [])
        error_code = result.data.get("error_code")
        if isinstance(error_code, str) and _ERROR_CODE_RE.fullmatch(error_code):
            output["error_code"] = error_code
        details = result.data.get("error_details")
        if isinstance(details, dict) and isinstance(details.get("candidates"), list):
            output["candidate_count"] = len(details["candidates"])
        return output

    def _build_result_summary(self, result: ToolResult) -> dict:
        summary: dict[str, Any] = {
            "kind": "external_tool",
            "title": self.binding.tool_label,
            "provider": MAPS_SERVICE,
            "truncated": False,
        }
        product_result = result.data.get("result")
        if isinstance(product_result, dict):
            if self.tool_name == LOCAL_PLACE_SEARCH:
                summary["result_count"] = len(product_result.get("places") or [])
            else:
                summary["mode_count"] = len(product_result.get("routes") or [])
        return summary

    def _failed_result(
        self,
        started_at: float,
        error_code: str,
        *,
        model_text: str = "",
        error_details: dict[str, Any] | None = None,
    ) -> ToolResult:
        data: dict[str, Any] = {
            "remote_tool_name": self.binding.remote_tool_name,
            "error_code": error_code if _ERROR_CODE_RE.fullmatch(error_code) else "internal_error",
            "model_text": model_text,
        }
        if error_details:
            data["error_details"] = error_details
        return ToolResult(
            status="failed",
            duration_ms=_duration_ms(started_at),
            data=data,
            error_message="地图查询失败",
        )


def _uses_current_location(args: dict) -> bool:
    return CURRENT_LOCATION in {args.get("anchor_source"), args.get("origin_source"), args.get("destination_source")}


def _geolocation_meta(context: ToolRuntimeContext | None) -> dict[str, Any] | None:
    """没有设备位置时不带 _meta，服务回 location_unavailable，让模型问用户在哪。"""
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


def _ambiguity_details(error: dict[str, Any]) -> dict[str, Any] | None:
    """重名候选交给“待确认”展示（place_choice）；没有候选就按普通失败。"""
    candidates = error.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    return {
        "place_name": str(error.get("place_name") or "")[:120],
        "candidate_argument": str(error.get("candidate_argument") or "")[:40],
        "candidates": candidates[:5],
    }


def _providers(product_result: dict[str, Any]) -> list[str]:
    providers = product_result.get("providers")
    return [item for item in providers if item in _PROVIDER_LABELS] if isinstance(providers, list) else []


def _attribution(product_result: dict[str, Any]) -> tuple[str, StructuredResultAttribution]:
    providers = _providers(product_result) or ["amap"]
    label = "、".join(_PROVIDER_LABELS[item] for item in providers)
    return ",".join(providers), StructuredResultAttribution(label=label)


def _card_limitations(base: str, codes: Any) -> list[str]:
    items = [base]
    for code in codes if isinstance(codes, list) else []:
        text = _LIMITATION_TEXTS.get(code) if isinstance(code, str) else None
        if text and text not in items:
            items.append(text)
    return items[:_CARD_LIMITATIONS_MAX]


def _place_block(product_result: dict[str, Any], *, status: str, block_id: str, log_id: str) -> PlaceResultsBlock:
    provider, attribution = _attribution(product_result)
    places = [
        _place(item)
        for item in (product_result.get("places") or [])[:5]
        if isinstance(item, dict) and _text(item.get("name"), 120)
    ]
    anchor = product_result.get("anchor")
    near = (
        "当前位置"
        if isinstance(anchor, dict) and anchor.get("source") == CURRENT_LOCATION
        else product_result.get("near")
    )
    return PlaceResultsBlock(
        type="place_results",
        id=block_id,
        schema_version=1,
        provider=provider,
        attribution=attribution,
        query=_text(product_result["query"], 80) or "地点",
        near=_text(near, 120),
        status=status,
        result_count=len(places),
        places=places,
        limitations=_card_limitations(_PLACE_LIMITATION, product_result.get("limitations")),
        tool_call_log_id=log_id,
    )


def _place(item: dict[str, Any]) -> PlaceResult:
    photos: list[PlacePhoto] = []
    if isinstance(item.get("photo_url"), str):
        try:
            photos.append(PlacePhoto(url=item["photo_url"]))
        except ValidationError:
            pass
    rating = _number(item.get("rating"))
    place = {
        "provider_place_id": _text(item.get("place_id"), 160),
        "name": _text(item.get("name"), 120),
        "address": _text(item.get("address"), 240),
        "district": _text(item.get("district"), 120),
        "category": _text(item.get("category"), 160),
        "distance_m": _int(item.get("distance_m"), 10_000_000),
        "photos": photos,
        "rating": rating if rating is not None and rating <= 5 else None,
        "reference_cost_yuan": _number(item.get("reference_cost_yuan")),
        "business_area": _text(item.get("business_area"), 120),
        "open_hours": _text(item.get("open_hours"), 240),
    }
    platform_url = item.get("map_url")
    if isinstance(platform_url, str):
        try:
            return PlaceResult(
                **place,
                platform_url=platform_url,
                actions=[StructuredResultAction(kind="open_external", label="查看详情", url=platform_url)],
            )
        except ValidationError:
            # 跳转链接不合规时只丢链接，地点照常显示。
            logger.warning("地图跳转链接不合规，已丢弃")
    return PlaceResult(**place)


def _route_block(product_result: dict[str, Any], *, status: str, block_id: str, log_id: str) -> RouteResultsBlock:
    provider, attribution = _attribution(product_result)
    raw_unavailable = product_result.get("unavailable_modes")
    return RouteResultsBlock(
        type="route_results",
        id=block_id,
        schema_version=1,
        provider=provider,
        attribution=attribution,
        status=status,
        origin=_endpoint(product_result["origin"]),
        destination=_endpoint(product_result["destination"]),
        routes=[_route(item) for item in product_result["routes"][:3]],
        unavailable_modes=[mode for mode in (raw_unavailable or [])[:3] if mode in _ROUTE_MODES],
        limitations=_card_limitations(_ROUTE_LIMITATION, product_result.get("limitations")),
        tool_call_log_id=log_id,
    )


def _endpoint(value: dict[str, Any]) -> RouteEndpoint:
    label = "当前位置" if value.get("source") == CURRENT_LOCATION else _text(value.get("label"), 120)
    return RouteEndpoint(label=label or "地点", city=_text(value.get("city"), 40))


def _route(item: dict[str, Any]) -> RouteOption:
    mode = item["mode"]
    transit = mode == "transit"
    return RouteOption(
        mode=mode,
        distance_m=_int(item.get("distance_m"), 100_000_000),
        duration_s=_int(item.get("duration_s"), 10_000_000),
        summary=_text(item.get("summary"), 160),
        toll_yuan=_number(item.get("toll_yuan")) if mode == "driving" else None,
        transfers=_int(item.get("transfers"), 100),
        transit_type=item.get("transit_type") if item.get("transit_type") in _TRANSIT_TYPES else None,
        walking_distance_m=_int(item.get("walking_distance_m"), 100_000_000),
        legs=_legs(item.get("legs")) if transit else [],
        alternatives=[_alternative(alt) for alt in (item.get("alternatives") or [])[:2]] if transit else [],
    )


def _alternative(item: dict[str, Any]) -> TransitAlternative:
    return TransitAlternative(
        transit_type=item.get("transit_type") if item.get("transit_type") in _TRANSIT_TYPES else None,
        duration_s=_int(item.get("duration_s"), 10_000_000),
        walking_distance_m=_int(item.get("walking_distance_m"), 100_000_000),
        transfers=_int(item.get("transfers"), 100),
        summary=_text(item.get("summary"), 160),
        legs=_legs(item.get("legs")),
    )


def _legs(value: Any) -> list[TransitLeg]:
    return [
        TransitLeg(
            kind=item.get("kind") if item.get("kind") in _LEG_KINDS else None,
            line_name=_text(item.get("line_name"), 120),
            departure_stop=_text(item.get("departure_stop"), 120),
            arrival_stop=_text(item.get("arrival_stop"), 120),
            via_stop_count=_int(item.get("via_stop_count"), 100),
            distance_m=_int(item.get("distance_m"), 100_000_000),
            duration_s=_int(item.get("duration_s"), 10_000_000),
            entrance=_text(item.get("entrance"), 80),
            exit=_text(item.get("exit"), 80),
        )
        for item in (value if isinstance(value, list) else [])[:8]
        if isinstance(item, dict)
    ]


def _text(value: Any, max_chars: int) -> str | None:
    """服务给的文字按卡片字段长度截断；空的当没有。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:max_chars]


def _int(value: Any, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:
        return None
    return int(value) if 0 <= value <= maximum else None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:
        return None
    return value if 0 <= value <= 1_000_000 else None


def _normalize_city(value: str) -> str:
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())
    for suffix in ("自治州", "地区", "市", "盟"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _city_matches(requested: str, candidate: str) -> bool:
    normalized = _normalize_city(requested)
    return bool(normalized) and normalized == _normalize_city(candidate)


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1_000))
