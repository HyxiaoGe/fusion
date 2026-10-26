"""模型调用的 token 用量与成本估算。

用量取自轨迹账本每轮 llm_round_completed 的 token 数（含前置对话轮），按该轮 llm_round_started
记录的模型计价；单价取 LiteLLM 代理登记的每 token 价格。命中缓存的输入按缓存价计，模型没登记
缓存价时按普通输入价计，这类模型算出的是上限。按时段计价的模型（DeepSeek）按这一轮开始的时刻
套用代理登记的 off_peak_pricing 规则，规则语义与 LiteLLM 一致：hours_utc 是 UTC 钟点区间（起含
止不含，可跨午夜，起止相同为整天），windows 里每条再限定星期几，星期按 weekday_timezone 的日历
看。不知道时刻时按标准（高峰）价计。成本是按登记单价的估算，不等于服务商账单。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import litellm_catalog
from app.db.models import AgentEvent, AgentSession

# 与代理登记的 *_above_200k_tokens 单价对应：单次请求输入超过这个数时整次按高档价计。
_TIERED_INPUT_TOKENS = 200_000

_WEEKDAY_NAMES = {
    "mon": 1, "monday": 1, "tue": 2, "tues": 2, "tuesday": 2, "wed": 3, "wednesday": 3,
    "thu": 4, "thur": 4, "thurs": 4, "thursday": 4, "fri": 5, "friday": 5,
    "sat": 6, "saturday": 6, "sun": 7, "sunday": 7,
}  # fmt: skip
_OFF_PEAK_RATE_KEYS = ("input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost")


def _window_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _in_utc_windows(windows: Sequence[str], at_utc: datetime) -> bool:
    now = at_utc.time()
    for window in windows:
        try:
            start_text, end_text = window.split("-")
            start = time.fromisoformat(start_text.strip())
            end = time.fromisoformat(end_text.strip())
        except ValueError:
            continue
        if start < end:
            if start <= now < end:
                return True
        elif now >= start or now < end:
            return True
    return False


def _weekday_of(at_utc: datetime, weekday_timezone: Any) -> int:
    calendar = timezone.utc
    if isinstance(weekday_timezone, str) and weekday_timezone.strip():
        try:
            calendar = ZoneInfo(weekday_timezone.strip())
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return at_utc.astimezone(calendar).isoweekday()


def _matches_weekdays(at_utc: datetime, weekdays: Any, weekday_timezone: Any) -> bool:
    if weekdays is None:
        return True
    if isinstance(weekdays, str) or not isinstance(weekdays, Sequence):
        return False
    allowed = set()
    for day in weekdays:
        if isinstance(day, bool):
            continue
        if isinstance(day, int) and 1 <= day <= 7:
            allowed.add(day)
        elif isinstance(day, str):
            allowed.add(_WEEKDAY_NAMES.get(day.strip().lower(), 0))
    return _weekday_of(at_utc, weekday_timezone) in allowed


def is_off_peak(off_peak: Mapping[str, Any], at: datetime) -> bool:
    """这一时刻是否落在空闲时段。naive 时间按 UTC 解释，和 LiteLLM 的约定一致。"""
    at_utc = at.astimezone(timezone.utc) if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    flat_windows = _window_strings(off_peak.get("hours_utc"))
    if flat_windows and _in_utc_windows(flat_windows, at_utc):
        return True
    rules = off_peak.get("windows")
    if isinstance(rules, str) or not isinstance(rules, Sequence):
        return False
    weekday_timezone = off_peak.get("weekday_timezone")
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        rule_windows = _window_strings(rule.get("hours_utc"))
        if not rule_windows or not _matches_weekdays(at_utc, rule.get("weekdays"), weekday_timezone):
            continue
        if _in_utc_windows(rule_windows, at_utc):
            return True
    return False


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    # 代理没登记单价的模型：这些轮次的 token 计入用量，但不计入成本。
    unpriced_models: tuple[str, ...] = field(default=())

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            unpriced_models=tuple(sorted({*self.unpriced_models, *other.unpriced_models})),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "unpriced_models": list(self.unpriced_models),
        }


def round_cost(
    costs: Mapping[str, Any],
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    at: datetime | None = None,
) -> float | None:
    input_price = costs.get("input_cost_per_token")
    output_price = costs.get("output_cost_per_token")
    if input_price is None or output_price is None:
        return None
    if input_tokens > _TIERED_INPUT_TOKENS:
        input_price = costs.get("input_cost_per_token_above_200k_tokens") or input_price
        output_price = costs.get("output_cost_per_token_above_200k_tokens") or output_price
    cache_price = costs.get("cache_read_input_token_cost")
    if cache_price is None:
        cache_price = input_price
    off_peak = costs.get("off_peak_pricing")
    if at is not None and isinstance(off_peak, Mapping) and is_off_peak(off_peak, at):
        # 空闲时段单价整个替换对应档位（含 200k 分档），没给的档位沿用标准价，与 LiteLLM 一致。
        rates = {key: off_peak.get(key) for key in _OFF_PEAK_RATE_KEYS}
        rates = {
            key: value
            for key, value in rates.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        input_price = rates.get("input_cost_per_token", input_price)
        output_price = rates.get("output_cost_per_token", output_price)
        cache_price = rates.get("cache_read_input_token_cost", cache_price)
    cached = min(cache_read_tokens, input_tokens)
    return (input_tokens - cached) * input_price + cached * cache_price + output_tokens * output_price


def usage_for_round(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    at: datetime | None = None,
) -> TokenUsage:
    entry = litellm_catalog.get_model_entry(model_id) or {}
    cost = round_cost(
        entry.get("token_costs") or {},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        at=at,
    )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cost_usd=cost or 0.0,
        unpriced_models=() if cost is not None else (model_id,),
    )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def load_runs_usage(db: Session, run_ids: Iterable[str]) -> TokenUsage:
    """汇总这些 run 里每一轮模型调用的用量与估算成本。"""
    run_ids = [run_id for run_id in run_ids if run_id]
    if not run_ids:
        return TokenUsage()
    run_models = dict(
        db.execute(select(AgentSession.id, AgentSession.model_id).where(AgentSession.id.in_(run_ids))).all()
    )
    rows = db.execute(
        select(AgentEvent.run_id, AgentEvent.event_type, AgentEvent.payload, AgentEvent.event_ts)
        .where(
            AgentEvent.run_id.in_(run_ids),
            AgentEvent.event_type.in_(("llm_round_started", "llm_round_completed")),
        )
        .order_by(AgentEvent.run_id, AgentEvent.sequence)
    ).all()
    round_models: dict[str, str] = {}
    round_started_at: dict[str, datetime] = {}
    total = TokenUsage()
    for run_id, event_type, payload, event_ts in rows:
        payload = payload or {}
        round_id = payload.get("llm_round_id")
        if event_type == "llm_round_started":
            if round_id and payload.get("model"):
                round_models[round_id] = payload["model"]
            if round_id and event_ts is not None:
                round_started_at[round_id] = event_ts
            continue
        # 换了备用模型的轮次按实际模型计价；老数据没有轮次模型时退回 run 绑定的模型。
        model_id = round_models.get(round_id) or run_models.get(run_id) or ""
        # 时段价按这一轮开始的时刻看；缺开始事件时退回完成时刻。
        total += usage_for_round(
            model_id,
            input_tokens=_int(payload.get("input_tokens")),
            output_tokens=_int(payload.get("output_tokens")),
            cache_read_tokens=_int(payload.get("cache_read_tokens")),
            at=round_started_at.get(round_id) or event_ts,
        )
    return total
