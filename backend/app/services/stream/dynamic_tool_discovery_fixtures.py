"""动态工具发现首轮假工具：真实 schema/结果合同，不调用付费供应商。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.ai.tools import build_url_read_tool, build_web_search_tool
from app.schemas.chat import (
    SearchBlock,
    SearchSourceSummary,
    SourceReference,
    StructuredResultAttribution,
    TrainOption,
    TrainResultsBlock,
    TravelEndpoint,
    TravelMoney,
    UrlBlock,
    WeatherForecastDay,
    WeatherResultsBlock,
)
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_DEFINITIONS
from app.services.stream.dynamic_tool_discovery import (
    NETWORK_KIND_LOCAL_READONLY,
    NETWORK_KIND_UNKNOWN_NETWORK,
    AuthorizedToolEntry,
    infer_network_kind,
)
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

_CHINA_TZ = timezone(timedelta(hours=8))
EXPERIMENT_NOW = datetime(2026, 9, 22, 9, 0, tzinfo=_CHINA_TZ)
SYNTHETIC_LIMITATION = "合成测试结果，非实时供应商数据，仅用于离线原型。"
MCP_READONLY_ALIAS = "mcp_readonly_probe"
MCP_NETWORK_ALIAS = "mcp_network_probe"


class SharedFixtureBudget:
    """同一 Run 内多个假工具共用的供应商额度；扩容不得重建。"""

    def __init__(self, *, max_calls: int = 4) -> None:
        self._max_calls = max_calls
        self._used = 0
        self._lock = asyncio.Lock()

    async def try_consume(self) -> bool:
        async with self._lock:
            if self._used >= self._max_calls:
                return False
            self._used += 1
            return True

    async def remaining(self) -> int:
        async with self._lock:
            return max(0, self._max_calls - self._used)

    async def is_exhausted(self) -> bool:
        return await self.remaining() == 0


class FixtureToolHandler(BaseToolHandler):
    supports_automatic_retry = False

    def __init__(
        self,
        *,
        tool_name: str,
        playback: dict[str, Any] | None = None,
        budget: SharedFixtureBudget | None = None,
        consume_budget: bool = False,
    ) -> None:
        self._tool_name = tool_name
        self.playback = playback or {}
        self.budget = budget
        self.controls = budget
        self.consume_budget = consume_budget
        self.execute_count = 0

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def sse_event_prefix(self) -> str:
        return self._tool_name

    async def is_run_budget_exhausted(self) -> bool:
        if self.budget is None:
            return False
        return await self.budget.is_exhausted()

    async def execute(self, args: dict) -> ToolResult:
        self.execute_count += 1
        scenario = self._scenario(args)
        if self.consume_budget and self.budget is not None:
            if not await self.budget.try_consume():
                return ToolResult(
                    status="failed",
                    data={"error_code": "travel_run_budget_exhausted", "retryable": False, "synthetic": True},
                    error_message="合成测试：供应商额度已耗尽",
                )
        builder = {
            "weather_forecast": self._weather,
            "search_trains": self._trains,
            "web_search": self._search,
            "url_read": self._url_read,
            MCP_READONLY_ALIAS: self._mcp,
            MCP_NETWORK_ALIAS: self._mcp,
        }.get(self._tool_name)
        if builder is None:
            return ToolResult(status="failed", data={"error_code": "unknown_fixture"}, error_message="未知假工具")
        return builder(args, scenario)

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        if result.status not in {"success", "degraded"}:
            return None
        payload = result.data.get("result")
        if self._tool_name == "weather_forecast" and isinstance(payload, dict):
            days = [WeatherForecastDay.model_validate(item) for item in payload["forecast_days"]]
            return WeatherResultsBlock(
                type="weather_results",
                id=block_id,
                schema_version=1,
                provider="amap",
                attribution=StructuredResultAttribution(label="合成测试天气"),
                status=result.status,
                query=payload["query"],
                resolved_location=payload["resolved_location"],
                day_count=len(days),
                forecast_days=days,
                fetched_at=payload["fetched_at"],
                limitations=list(payload["limitations"]),
                tool_call_log_id=log_id,
            )
        if self._tool_name == "search_trains" and isinstance(payload, dict):
            trains = [_train_option(item) for item in payload["items"]]
            return TrainResultsBlock(
                type="train_results",
                id=block_id,
                schema_version=1,
                provider="flyai",
                attribution=StructuredResultAttribution(label="合成测试车次"),
                status=result.status,
                origin=payload["origin"],
                destination=payload["destination"],
                departure_date=payload["departure_date"],
                observed_at=payload["observed_at"],
                result_count=len(trains),
                trains=trains,
                limitations=list(payload["limitations"]),
                tool_call_log_id=log_id,
            )
        if self._tool_name == "web_search":
            sources = [
                SearchSourceSummary(
                    url=item["url"],
                    title=str(item.get("title") or item["url"]),
                )
                for item in result.data.get("sources") or []
            ]
            return SearchBlock(
                type="search",
                id=block_id,
                status=result.status,
                query=str(result.data.get("query") or ""),
                sources=sources,
                source_count=len(sources),
                source_refs=[
                    SourceReference(
                        kind="search",
                        title=source.title,
                        url=source.url,
                        status=result.status,
                        tool_call_log_id=log_id,
                    )
                    for source in sources
                ],
                tool_call_log_id=log_id,
            )
        if self._tool_name == "url_read":
            url = str(result.data.get("url") or "")
            source_refs = (
                [
                    SourceReference(
                        kind="url_read",
                        title=str(result.data.get("title") or ""),
                        url=url,
                        status=result.status,
                        tool_call_log_id=log_id,
                    )
                ]
                if url and result.status == "success"
                else []
            )
            return UrlBlock(
                type="url_read",
                id=block_id,
                status=result.status,
                url=url,
                title=result.data.get("title"),
                tool_call_log_id=log_id,
                source_count=len(source_refs),
                source_refs=source_refs,
            )
        return None

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        import json

        return json.dumps(
            {"status": result.status, "synthetic": True, "data": result.data, "error": result.error_message},
            ensure_ascii=False,
            default=str,
        )

    def _scenario(self, args: dict) -> str:
        if self.playback.get("scenario"):
            return str(self.playback["scenario"])
        location = str(args.get("location") or args.get("destination") or args.get("query") or args.get("url") or "")
        if "empty" in location or location.endswith("/empty"):
            return "empty"
        if "error" in location or location.endswith("/error"):
            return "error"
        if "timeout" in location:
            return "timeout"
        if "nobody" in location or "no-body" in location:
            return "url_empty"
        return "success"

    def _weather(self, args: dict, scenario: str) -> ToolResult:
        location = str(args.get("location") or "杭州")
        if scenario == "error":
            return ToolResult(
                status="failed",
                data={"error_code": "weather_unavailable", "retryable": True, "synthetic": True},
                error_message="合成测试：天气查询失败",
            )
        if scenario == "timeout":
            return ToolResult(
                status="failed",
                data={"error_code": "timeout", "retryable": True, "synthetic": True},
                error_message="合成测试：天气查询超时",
            )
        if scenario == "empty":
            return ToolResult(
                status="failed",
                data={"error_code": "empty_result", "retryable": False, "synthetic": True, "result": None},
                error_message="合成测试：天气空结果",
            )
        today = EXPERIMENT_NOW.date()
        days = [_forecast_day(today + timedelta(days=offset), 24 + offset, 16 + offset) for offset in range(4)]
        payload = {
            "query": location,
            "resolved_location": location,
            "forecast_days": [day.model_dump() for day in days],
            "fetched_at": EXPERIMENT_NOW,
            "limitations": [SYNTHETIC_LIMITATION],
            "day_count": len(days),
        }
        return ToolResult(status="success", data={"result": payload, "synthetic": True})

    def _trains(self, args: dict, scenario: str) -> ToolResult:
        origin = str(args.get("origin") or "杭州")
        destination = str(args.get("destination") or "上海")
        departure_date = str(args.get("departure_date") or EXPERIMENT_NOW.date().isoformat())
        observed = EXPERIMENT_NOW
        if scenario == "error":
            return ToolResult(
                status="failed",
                data={"error_code": "train_unavailable", "retryable": True, "synthetic": True},
                error_message="合成测试：车次查询失败",
            )
        if scenario in {"empty", "url_empty"}:
            payload = {
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
                "observed_at": observed,
                "items": [],
                "limitations": [SYNTHETIC_LIMITATION, "空结果，无合成班次。"],
            }
            return ToolResult(status="success", data={"result": payload, "synthetic": True})
        item = {
            "option_id": "syn-g7301",
            "train_no": "G7301",
            "train_type": "高速",
            "departure": {
                "city": origin,
                "station_name": origin,
                "scheduled_at": datetime.fromisoformat(f"{departure_date}T08:00:00").replace(tzinfo=_CHINA_TZ),
            },
            "arrival": {
                "city": destination,
                "station_name": destination,
                "scheduled_at": datetime.fromisoformat(f"{departure_date}T09:05:00").replace(tzinfo=_CHINA_TZ),
            },
            "duration_s": 3900,
            "seat_class": "二等座",
            "stops": 0,
            "price": {"currency": "CNY", "amount_minor": 7300},
        }
        payload = {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "observed_at": observed,
            "items": [item],
            "limitations": [SYNTHETIC_LIMITATION],
        }
        return ToolResult(status="success", data={"result": payload, "synthetic": True})

    def _search(self, args: dict, scenario: str) -> ToolResult:
        query = str(args.get("query") or "")
        if scenario == "error":
            return ToolResult(
                status="failed",
                data={"error_code": "search_failed", "query": query, "synthetic": True},
                error_message="合成测试：搜索失败",
            )
        if scenario == "empty":
            return ToolResult(status="success", data={"query": query, "sources": [], "synthetic": True})
        return ToolResult(
            status="success",
            data={
                "query": query,
                "synthetic": True,
                "sources": [
                    {
                        "url": "https://example.test/hangzhou-weather",
                        "title": "杭州周末天气（合成）",
                        "snippet": "合成摘要，不是真实搜索结果。",
                    }
                ],
            },
        )

    def _url_read(self, args: dict, scenario: str) -> ToolResult:
        url = str(args.get("url") or "https://example.test/")
        if scenario == "error":
            return ToolResult(
                status="failed",
                data={"error_code": "url_read_failed", "url": url, "synthetic": True},
                error_message="合成测试：读取失败",
            )
        if scenario in {"empty", "url_empty"}:
            return ToolResult(
                status="success",
                data={"url": url, "title": "空正文页", "content": "", "synthetic": True},
            )
        return ToolResult(
            status="success",
            data={"url": url, "title": "合成正文", "content": "这是合成页面正文，仅供离线测试。", "synthetic": True},
        )

    def _mcp(self, args: dict, scenario: str) -> ToolResult:
        return ToolResult(
            status="success",
            data={
                "alias": MCP_READONLY_ALIAS,
                "readonly": True,
                "synthetic": True,
                "echo": dict(args),
                "scenario": scenario,
            },
        )


def _forecast_day(day: date, high_c: float, low_c: float) -> WeatherForecastDay:
    return WeatherForecastDay(
        date=day,
        weekday=day.isoweekday(),
        day_weather="多云",
        night_weather="晴",
        high_c=high_c,
        low_c=low_c,
    )


def _train_option(item: dict) -> TrainOption:
    return TrainOption(
        option_id=item["option_id"],
        train_no=item["train_no"],
        train_type=item.get("train_type"),
        departure=TravelEndpoint.model_validate(item["departure"]),
        arrival=TravelEndpoint.model_validate(item["arrival"]),
        duration_s=item["duration_s"],
        seat_class=item.get("seat_class"),
        stops=0,
        price=TravelMoney.model_validate(item["price"]) if item.get("price") else None,
    )


def _schema_by_name(definitions: list[dict], name: str) -> dict:
    for item in definitions:
        function = item.get("function") if isinstance(item, dict) else None
        if isinstance(function, dict) and function.get("name") == name:
            return item
    raise KeyError(name)


def build_prototype_fixture_catalog(
    *,
    budget: SharedFixtureBudget | None = None,
    playback: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict], dict[str, FixtureToolHandler], SharedFixtureBudget]:
    """返回 schema、handler、共享预算；不修改生产 MCP 配置。"""

    shared = budget or SharedFixtureBudget(max_calls=2)
    play = playback or {}
    handlers = {
        "weather_forecast": FixtureToolHandler(
            tool_name="weather_forecast",
            playback=play.get("weather_forecast"),
            budget=shared,
            consume_budget=True,
        ),
        "search_trains": FixtureToolHandler(
            tool_name="search_trains",
            playback=play.get("search_trains"),
            budget=shared,
            consume_budget=True,
        ),
        "web_search": FixtureToolHandler(
            tool_name="web_search",
            playback=play.get("web_search"),
        ),
        "url_read": FixtureToolHandler(
            tool_name="url_read",
            playback=play.get("url_read"),
        ),
        MCP_READONLY_ALIAS: FixtureToolHandler(
            tool_name=MCP_READONLY_ALIAS,
            playback=play.get(MCP_READONLY_ALIAS),
        ),
        MCP_NETWORK_ALIAS: FixtureToolHandler(
            tool_name=MCP_NETWORK_ALIAS,
            playback=play.get(MCP_NETWORK_ALIAS),
        ),
    }
    mcp_schema = {
        "type": "function",
        "function": {
            "name": MCP_READONLY_ALIAS,
            "description": "测试用途只读 MCP 别名，回显参数，无副作用。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"note": {"type": "string"}},
            },
        },
    }
    mcp_network_schema = {
        "type": "function",
        "function": {
            "name": MCP_NETWORK_ALIAS,
            "description": "测试用途网络型 MCP 别名，未知网络属性，禁网时应失败关闭。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"note": {"type": "string"}},
            },
        },
    }
    schemas = [
        _schema_by_name(AMAP_PRODUCT_DEFINITIONS, "weather_forecast"),
        _schema_by_name(FLYAI_TRAVEL_DEFINITIONS, "search_trains"),
        build_web_search_tool(),
        build_url_read_tool(),
        mcp_schema,
        mcp_network_schema,
    ]
    return schemas, handlers, shared


def fixture_entries(
    *,
    budget: SharedFixtureBudget | None = None,
    playback: dict[str, dict[str, Any]] | None = None,
    include: list[str] | None = None,
) -> dict[str, AuthorizedToolEntry]:
    schemas, handlers, _shared = build_prototype_fixture_catalog(budget=budget, playback=playback)
    wanted = set(include or handlers)
    entries: dict[str, AuthorizedToolEntry] = {}
    for schema in schemas:
        name = schema["function"]["name"]
        if name not in wanted:
            continue
        binding = (
            {"alias": name, "provider": "fixture", "tool_label": "synthetic"}
            if name in {MCP_READONLY_ALIAS, MCP_NETWORK_ALIAS}
            else None
        )
        kind = NETWORK_KIND_LOCAL_READONLY if name == MCP_READONLY_ALIAS else infer_network_kind(name, binding=binding)
        if name == MCP_NETWORK_ALIAS:
            kind = NETWORK_KIND_UNKNOWN_NETWORK
        entries[name] = AuthorizedToolEntry(
            name=name,
            summary=str(schema["function"].get("description") or name).split("\n", 1)[0][:180],
            schema=schema,
            handler=handlers[name],
            binding=binding,
            network_kind=kind,
        )
    return entries
