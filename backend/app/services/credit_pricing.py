"""Credits 计价：一个 run 的模型消耗与工具消耗。

- 模型：轨迹账本里每一轮的 token 数，按这一轮实际使用的模型、开始时刻的单价计（含缓存命中价、
  DeepSeek 时段价），成本按 CREDITS_USD_PER_CREDIT 折算成 credits。
- 工具：成功（含部分成功）的调用按次计固定 credits，失败不计。工具搜到的内容进入上下文后已按
  下一轮的输入 token 计过，这里不再叠加。单价按外部服务的成本与额度稀缺程度定，可用
  CREDITS_TOOL_PRICES 覆盖；没有定价的工具不计。MCP 工具对模型的名字是哈希别名，按所属
  服务的 provider 定价（键为 ``mcp:<provider>``）。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import litellm_catalog
from app.core.config import settings
from app.db.models import AgentEvent, McpServer, ToolCallLog
from app.evals.usage import is_off_peak, usage_for_round
from app.services.mcp.tool_contract import build_agent_tool_alias, build_tool_label

logger = logging.getLogger(__name__)

DEFAULT_TOOL_PRICES: dict[str, float] = {
    # 联网搜索走 DeepSeek 搜索，官方 5 元/千次成功请求
    "web_search": 8,
    "url_read": 5,
    "image_search": 5,
    "video_search": 5,
    # 生图按张计，Seedream 每张约 0.2 元
    "generate_image": 300,
    "mcp:image-service": 300,
    # 地图、票务、天气等产品工具消耗服务商的月度/日额度
    "route_compare": 2,
    "local_place_search": 2,
    "weather_forecast": 1,
    "search_trains": 2,
    "search_flights": 2,
    "mcp:amap": 2,
    "mcp:tencent_map": 2,
    "mcp:context7": 1,
    "mcp:microsoft": 1,
    "mcp:trendradar": 1,
}

_CHARGEABLE_TOOL_STATUSES = ("success", "degraded")
# 消耗档：按「输入×3 + 输出」÷4 的混合单价（美元/百万 token），Agent 运行以输入为主
_COST_TIER_BOUNDS = ((0.3, "low"), (2.5, "mid"))


@lru_cache(maxsize=4)
def _tool_prices(override: str) -> dict[str, float]:
    prices = dict(DEFAULT_TOOL_PRICES)
    if override.strip():
        try:
            parsed = json.loads(override)
        except ValueError:
            logger.warning("credits_tool_prices_invalid_json")
            parsed = {}
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
                    prices[key] = max(float(value), 0.0)
    return prices


def tool_prices() -> dict[str, float]:
    return _tool_prices(settings.CREDITS_TOOL_PRICES)


def credits_from_usd(cost_usd: float) -> float:
    return cost_usd / settings.CREDITS_USD_PER_CREDIT


def cost_tier(token_costs: dict[str, Any]) -> str | None:
    """按标准单价把模型分成 low / mid / high；没登记单价返回 None。"""
    input_price = token_costs.get("input_cost_per_token")
    output_price = token_costs.get("output_cost_per_token")
    if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
        return None
    blended = (input_price * 3 + output_price) / 4 * 1_000_000
    for bound, tier in _COST_TIER_BOUNDS:
        if blended <= bound:
            return tier
    return "high"


@dataclass
class RunCharge:
    rounds: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)

    @property
    def model_credits(self) -> float:
        return sum(item["credits"] for item in self.rounds)

    @property
    def tool_credits(self) -> float:
        return sum(item["credits"] for item in self.tools)

    @property
    def total(self) -> float:
        return round(self.model_credits + self.tool_credits, 4)

    def breakdown(self) -> dict[str, Any]:
        return {
            "model_credits": round(self.model_credits, 4),
            "tool_credits": round(self.tool_credits, 4),
            "rounds": self.rounds,
            "tools": self.tools,
        }


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _round_entries(db: Session, run_id: str, fallback_model_id: str | None) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AgentEvent.event_type, AgentEvent.payload, AgentEvent.event_ts)
        .where(
            AgentEvent.run_id == run_id,
            AgentEvent.event_type.in_(("llm_round_started", "llm_round_completed")),
        )
        .order_by(AgentEvent.sequence)
    ).all()
    started: dict[str, tuple[str | None, datetime | None]] = {}
    entries: list[dict[str, Any]] = []
    for event_type, payload, event_ts in rows:
        payload = payload or {}
        round_id = payload.get("llm_round_id")
        if event_type == "llm_round_started":
            if round_id:
                started[round_id] = (payload.get("model"), event_ts)
            continue
        model_id, at = started.get(round_id, (None, None))
        # 换了备用模型的轮次按实际模型计价；缺开始事件时退回 run 绑定的模型和完成时刻
        model_id = model_id or fallback_model_id or ""
        at = at or event_ts
        input_tokens = _int(payload.get("input_tokens"))
        output_tokens = _int(payload.get("output_tokens"))
        cache_read_tokens = _int(payload.get("cache_read_tokens"))
        usage = usage_for_round(
            model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            at=at,
        )
        off_peak_rule = ((litellm_catalog.get_model_entry(model_id) or {}).get("token_costs") or {}).get(
            "off_peak_pricing"
        )
        entries.append(
            {
                "model": model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "credits": round(credits_from_usd(usage.cost_usd), 4),
                "off_peak": bool(isinstance(off_peak_rule, dict) and at is not None and is_off_peak(off_peak_rule, at)),
                "unpriced": bool(usage.unpriced_models),
            }
        )
    return entries


def _mcp_aliases(db: Session) -> dict[str, tuple[str, str]]:
    """MCP 工具哈希别名 → (provider, 展示名)。"""
    aliases: dict[str, tuple[str, str]] = {}
    for server in db.execute(select(McpServer)).scalars():
        for snapshot in server.discovered_tools or []:
            name = snapshot.get("name") if isinstance(snapshot, dict) else None
            if isinstance(name, str) and name:
                aliases[build_agent_tool_alias(str(server.id), name)] = (
                    str(server.provider),
                    build_tool_label(server.name, name),
                )
    return aliases


def _tool_entries(db: Session, run_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ToolCallLog.tool_name, func.count())
        .where(ToolCallLog.trace_id == run_id, ToolCallLog.status.in_(_CHARGEABLE_TOOL_STATUSES))
        .group_by(ToolCallLog.tool_name)
    ).all()
    if not rows:
        return []
    prices = tool_prices()
    aliases = _mcp_aliases(db) if any(name.startswith("mcp_") for name, _ in rows) else {}
    counts: dict[tuple[str, str, float], int] = defaultdict(int)
    for name, count in rows:
        if name in aliases:
            provider, label = aliases[name]
            key = (f"mcp:{provider}", label, prices.get(f"mcp:{provider}", 0.0))
        else:
            key = (name, name, prices.get(name, 0.0))
        counts[key] += int(count)
    return [
        {"tool": tool, "label": label, "count": count, "unit_credits": unit, "credits": round(unit * count, 4)}
        for (tool, label, unit), count in sorted(counts.items())
    ]


def compute_run_charge(db: Session, run_id: str, *, fallback_model_id: str | None) -> RunCharge:
    return RunCharge(rounds=_round_entries(db, run_id, fallback_model_id), tools=_tool_entries(db, run_id))
