"""评测用例的 token 用量与成本估算。

被测模型的用量取自轨迹账本每轮 llm_round_completed 的 token 数（含前置对话轮），按该轮
llm_round_started 记录的模型计价；单价取 LiteLLM 代理登记的每 token 价格。命中缓存的输入按
缓存价计，模型没登记缓存价时按普通输入价计，这类模型算出的是上限。成本只用来比较用例和
模型之间的花费，不等于服务商账单。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import litellm_catalog
from app.db.models import AgentEvent, AgentSession

# 与代理登记的 *_above_200k_tokens 单价对应：单次请求输入超过这个数时整次按高档价计。
_TIERED_INPUT_TOKENS = 200_000


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
    costs: Mapping[str, Any], *, input_tokens: int, output_tokens: int, cache_read_tokens: int
) -> float | None:
    input_price = costs.get("input_cost_per_token")
    output_price = costs.get("output_cost_per_token")
    if input_price is None or output_price is None:
        return None
    if input_tokens > _TIERED_INPUT_TOKENS:
        input_price = costs.get("input_cost_per_token_above_200k_tokens") or input_price
        output_price = costs.get("output_cost_per_token_above_200k_tokens") or output_price
    cached = min(cache_read_tokens, input_tokens)
    cache_price = costs.get("cache_read_input_token_cost")
    if cache_price is None:
        cache_price = input_price
    return (input_tokens - cached) * input_price + cached * cache_price + output_tokens * output_price


def usage_for_round(model_id: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> TokenUsage:
    entry = litellm_catalog.get_model_entry(model_id) or {}
    cost = round_cost(
        entry.get("token_costs") or {},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
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
        select(AgentEvent.run_id, AgentEvent.event_type, AgentEvent.payload)
        .where(
            AgentEvent.run_id.in_(run_ids),
            AgentEvent.event_type.in_(("llm_round_started", "llm_round_completed")),
        )
        .order_by(AgentEvent.run_id, AgentEvent.sequence)
    ).all()
    round_models: dict[str, str] = {}
    total = TokenUsage()
    for run_id, event_type, payload in rows:
        payload = payload or {}
        round_id = payload.get("llm_round_id")
        if event_type == "llm_round_started":
            if round_id and payload.get("model"):
                round_models[round_id] = payload["model"]
            continue
        # 换了备用模型的轮次按实际模型计价；老数据没有轮次模型时退回 run 绑定的模型。
        model_id = round_models.get(round_id) or run_models.get(run_id) or ""
        total += usage_for_round(
            model_id,
            input_tokens=_int(payload.get("input_tokens")),
            output_tokens=_int(payload.get("output_tokens")),
            cache_read_tokens=_int(payload.get("cache_read_tokens")),
        )
    return total
