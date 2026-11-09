"""search_flights / search_trains：经 agent-tools 网关调用出行服务（flyai）的同名 MCP 工具。

出发日期规则、地名识别、筛选与排序、最多 5 条、limitations、给模型的结果 JSON（去掉了预订链接）和失败说明
都在服务里（agent-tools services/flyai）。这里只做 Fusion 自己的事：
- 说明用服务原文，后面接 Fusion 的约定（flyai.product_notes：卡片、回答写法、组合行程）；服务目录里没有就不公告。
- 一个 Run 里两个工具合计的调用上限，并在 Run 内排队（服务对同一用户同时只跑一个查询）。
- 结果画成航班 / 高铁卡片：服务的 limitations 是英文代码，卡片按中文显示；时刻是当地时间、不带时区，
  按北京时间补上（和原来 REST 接口的做法一样）。present_itinerary 引用卡片里的 option_id。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import (
    FlightOption,
    FlightResultsBlock,
    StructuredResultAction,
    StructuredResultAttribution,
    TrainOption,
    TrainResultsBlock,
    TravelEndpoint,
    TravelMoney,
)
from app.services.external.agent_tools_mcp import (
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
)
from app.services.mcp.server_service import MCP_TOOL_UNAVAILABLE_MESSAGE
from app.services.mcp.tool_contract import canonical_json_bytes, validate_tool_arguments
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext
from app.utils.time import CHINA_TZ

FLYAI_SERVICE = "flyai"
FLYAI_SEARCH_FLIGHTS = "search_flights"
FLYAI_SEARCH_TRAINS = "search_trains"
FLYAI_TRAVEL_TOOL_NAMES = frozenset({FLYAI_SEARCH_FLIGHTS, FLYAI_SEARCH_TRAINS})
# 服务排队最多 6 秒、查询最多 20 秒，网关给 flyai 30 秒。
_CALL_TIMEOUT_SECONDS = 30.0
_MAX_CONTEXT_BYTES = 12_000
# 与出行服务放行的预订主机一致；服务实际返回的多是 router.feizhu.com 短链。
_BOOKING_HOSTS = frozenset({"a.feizhu.com", "router.feizhu.com"})
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOOL_LABELS = {FLYAI_SEARCH_FLIGHTS: "航班查询", FLYAI_SEARCH_TRAINS: "高铁查询"}
_CARD_LIMITATIONS_MAX = 8
_FIXED_LIMITATIONS = (
    "班次与参考价格仅代表本次查询时刻，预订前请再次核实",
    "本次结果不包含余票、准点率、退改签、行李、登机口、检票口或站台信息",
)
# 服务 limitations 代码 → 卡片上的中文；没列的代码不显示。
_LIMITATION_TEXTS = {
    FLYAI_SEARCH_FLIGHTS: {
        "filtered_partial_list": "本次只取到部分航班，按条件筛选后可能还有其他符合条件的航班",
        "other_class_returned": "部分航班的参考价对应的不是所选舱位",
    },
    FLYAI_SEARCH_TRAINS: {
        "filtered_partial_list": "本次只取到部分车次，按条件筛选后可能还有其他符合条件的车次",
        "other_class_returned": "部分车次的参考价对应的不是所选座席",
        "high_speed_not_covered": "本次结果没有取到高铁或动车，该线路可能另有高铁、动车班次",
    },
}


class FlyAiTravelRunControls:
    """单次 Agent run 内两个出行工具共享的调用预算与并发门禁。"""

    def __init__(self, *, max_calls: int = 4, concurrency: int = 2) -> None:
        if max_calls < 1 or concurrency < 1:
            raise ValueError("FlyAI 出行工具预算配置无效")
        self._max_calls = max_calls
        self._used = 0
        self._budget_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(concurrency)
        self.concurrency_limit = concurrency

    async def try_consume(self) -> bool:
        async with self._budget_lock:
            if self._used >= self._max_calls:
                return False
            self._used += 1
            return True

    async def remaining(self) -> int:
        async with self._budget_lock:
            return max(0, self._max_calls - self._used)

    async def is_exhausted(self) -> bool:
        return await self.remaining() == 0


@dataclass(frozen=True)
class FlyAiTravelToolBinding:
    alias: str
    remote_tool_name: str
    provider: Literal["flyai"]
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


def build_flyai_travel_definition(spec: GatewayToolSpec) -> dict[str, Any]:
    return build_gateway_tool_definition(
        spec,
        name=spec.name,
        product_notes=render_runtime_prompt("flyai.product_notes"),
    )


def build_flyai_travel_binding(definition: dict[str, Any]) -> FlyAiTravelToolBinding:
    tool_name = definition["function"]["name"]
    return FlyAiTravelToolBinding(
        alias=tool_name,
        remote_tool_name=tool_name,
        provider="flyai",
        tool_label=_TOOL_LABELS[tool_name],
        definition_sha256=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
    )


class FlyAiTravelToolHandler(BaseToolHandler):
    # 服务里只有 busy 可重试，而且服务的说明已经告诉模型能不能再查一次；这里不自动重试。
    supports_automatic_retry = False

    def __init__(
        self,
        *,
        binding: FlyAiTravelToolBinding,
        parameters: dict[str, Any],
        controls: FlyAiTravelRunControls,
        call_tool: Any = call_gateway_tool,
        max_llm_context_bytes: int = _MAX_CONTEXT_BYTES,
    ) -> None:
        self.binding = binding
        self.parameters = parameters
        self.controls = controls
        self.call_tool = call_tool
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
        """只按公告的 schema 校验形状；日期是否过去、地名认不认识由服务判断并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(self.parameters, args)

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        """相同出行查询成功后可在当前 Agent run 内直接复用。

        只做服务也认为等价的归一：地名去首尾空白、值为 null 的可选参数当作没传；默认值由服务决定，这里不补。
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
        async with self.controls.semaphore:
            if not await self.controls.try_consume():
                return self._failed_result(started_at, "travel_run_budget_exhausted")
            outcome: GatewayToolOutcome = await self.call_tool(
                FLYAI_SERVICE,
                self.binding.remote_tool_name,
                args,
                scene=self.tool_name,
                min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
            )
        if outcome.ok and not outcome.model_text:
            outcome = GatewayToolOutcome(ok=False, error_code="invalid_response")
        if not outcome.ok:
            error_code = outcome.error_code or "tool_error"
            if not outcome.model_text:
                logger.warning("出行服务没有答复: tool=%s error_code=%s", self.tool_name, error_code)
            return self._failed_result(started_at, error_code, model_text=outcome.model_text)
        services = outcome.data.get("services")
        return ToolResult(
            status="success",
            duration_ms=_duration_ms(started_at),
            data={
                "remote_tool_name": self.binding.remote_tool_name,
                "result": outcome.data,
                "result_count": len(services) if isinstance(services, list) else 0,
                "model_text": outcome.model_text,
            },
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        if result.status != "success":
            return None
        product_result = result.data.get("result")
        if not isinstance(product_result, dict):
            return None
        try:
            query = product_result["query"]
            services = product_result["services"]
            common = {
                "id": block_id,
                "schema_version": 1,
                "provider": "flyai",
                "attribution": StructuredResultAttribution(label="飞猪旅行"),
                "status": "success",
                "origin": query["origin"],
                "destination": query["destination"],
                "departure_date": query["departure_date"],
                "observed_at": product_result["observed_at"],
                "result_count": len(services),
                "limitations": _card_limitations(self.tool_name, product_result),
                "tool_call_log_id": log_id,
            }
            if self.tool_name == FLYAI_SEARCH_FLIGHTS:
                return FlightResultsBlock(
                    type="flight_results",
                    flights=[_build_flight_option(item) for item in services],
                    **common,
                )
            return TrainResultsBlock(
                type="train_results",
                trains=[_build_train_option(item) for item in services],
                **common,
            )
        except (KeyError, TypeError, ValidationError, ValueError):
            logger.warning("出行结果画不成卡片: tool=%s", self.tool_name)
            return None

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        del citation_numbers
        model_text = result.data.get("model_text")
        model_text = model_text if isinstance(model_text, str) else ""
        if result.status != "success":
            # 服务答了就用服务给模型的说明（换城市、问日期、别重复查等）；网关或网络出错时用 Fusion 的说明。
            text = model_text or render_runtime_prompt("flyai.unavailable")
            return text.encode()[: self.max_llm_context_bytes].decode(errors="ignore")
        rendered = render_runtime_prompt(
            "flyai.result_wrapper",
            payload="__FUSION_PAYLOAD__",
            follow_up=render_runtime_prompt("flyai.follow_up"),
        )
        prefix, marker, suffix = rendered.partition("__FUSION_PAYLOAD__")
        if not marker:
            raise ValueError("出行 Prompt 缺少 payload 占位符")
        payload = escape(model_text, quote=False)
        budget = max(0, self.max_llm_context_bytes - len(prefix.encode()) - len(suffix.encode()))
        encoded = payload.encode()
        if len(encoded) > budget:
            payload = encoded[:budget].decode(errors="ignore")
        return f"{prefix}{payload}{suffix}"

    def sanitize_input_params_for_event(self, input_params: dict) -> dict:
        return {"argument_count": len(input_params) if isinstance(input_params, dict) else 0}

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        return {
            "tool_name": self.tool_name,
            "argument_count": len(input_params) if isinstance(input_params, dict) else 0,
        }

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        output = {
            "tool_name": self.tool_name,
            "status": result.status,
            "result_count": _safe_non_negative_int(result.data.get("result_count")),
        }
        error_code = result.data.get("error_code")
        if isinstance(error_code, str) and _ERROR_CODE_RE.fullmatch(error_code):
            output["error_code"] = error_code
        return {key: value for key, value in output.items() if value is not None}

    def _build_result_summary(self, result: ToolResult) -> dict:
        return {
            "kind": "external_tool",
            "title": self.binding.tool_label,
            "provider": "travel",
            "status": result.status,
            "result_count": _safe_non_negative_int(result.data.get("result_count")) or 0,
            "truncated": False,
        }

    def _failed_result(self, started_at: float, error_code: str, *, model_text: str = "") -> ToolResult:
        return ToolResult(
            status="failed",
            duration_ms=_duration_ms(started_at),
            data={
                "remote_tool_name": self.binding.remote_tool_name,
                "error_code": error_code if _ERROR_CODE_RE.fullmatch(error_code) else "internal_error",
                "model_text": model_text,
            },
            error_message=MCP_TOOL_UNAVAILABLE_MESSAGE,
        )


def _card_limitations(tool_name: str, product_result: dict[str, Any]) -> list[str]:
    items: list[str] = list(_FIXED_LIMITATIONS)
    codes = product_result.get("limitations")
    texts = _LIMITATION_TEXTS[tool_name]
    for code in codes if isinstance(codes, list) else []:
        text = texts.get(code) if isinstance(code, str) else None
        if text and text not in items:
            items.append(text)
    raw_query = product_result.get("query")
    query: dict[str, Any] = raw_query if isinstance(raw_query, dict) else {}
    arrival_before_hour = query.get("arrival_before_hour")
    if isinstance(arrival_before_hour, int) and not isinstance(arrival_before_hour, bool):
        items.append(f"结果已限定为出发日当天 {arrival_before_hour:02d}:00 前到达")
    if tool_name == FLYAI_SEARCH_TRAINS and query.get("train_category") == "high_speed":
        items.append("结果已限定为 G、D、C 字头的高铁或动车班次")
    return items[:_CARD_LIMITATIONS_MAX]


def _trusted_booking_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in _BOOKING_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
        and not parsed.fragment
    )


def _local_time(value: Any) -> datetime:
    """服务给的是当地时刻（YYYY-MM-DDTHH:MM），不带时区时按北京时间。"""
    if not isinstance(value, str):
        raise ValueError("班次时刻无效")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=CHINA_TZ)


def _build_endpoint(value: dict[str, Any]) -> TravelEndpoint:
    return TravelEndpoint(
        city=value["city"],
        station_name=value["station"],
        terminal=value.get("terminal"),
        scheduled_at=_local_time(value["time"]),
    )


def _build_money(value: Any) -> TravelMoney | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return TravelMoney(currency="CNY", amount_minor=round(value * 100))


def _build_action(item: dict[str, Any]) -> list[StructuredResultAction]:
    booking_url = item.get("booking_url")
    if not _trusted_booking_url(booking_url):
        return []
    return [StructuredResultAction(kind="open_external", label="查看详情", url=booking_url)]


def _build_flight_option(item: dict[str, Any]) -> FlightOption:
    return FlightOption(
        option_id=item["option_id"],
        airline_name=item.get("airline"),
        flight_no=item["number"],
        departure=_build_endpoint(item["departure"]),
        arrival=_build_endpoint(item["arrival"]),
        duration_s=item["duration_minutes"] * 60,
        cabin_class=item.get("travel_class"),
        stops=0,
        price=_build_money(item.get("price_yuan")),
        actions=_build_action(item),
    )


def _build_train_option(item: dict[str, Any]) -> TrainOption:
    return TrainOption(
        option_id=item["option_id"],
        train_no=item["number"],
        train_type=item.get("train_type"),
        departure=_build_endpoint(item["departure"]),
        arrival=_build_endpoint(item["arrival"]),
        duration_s=item["duration_minutes"] * 60,
        seat_class=item.get("travel_class"),
        stops=0,
        price=_build_money(item.get("price_yuan")),
        actions=_build_action(item),
    )


def _safe_non_negative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))
