"""行程卡片输出工具 present_itinerary。

模型从本轮已返回的航班/高铁班次里自己选定方案（去程、返程或多段）并说明理由，
服务端只校验引用的班次确实存在、不重复、按时间先后衔接，然后渲染卡片；
不替模型挑班次、配往返或按固定规则排序。只查票时模型不调用，完整结果照常展示。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import (
    FlightOption,
    FlightResultsBlock,
    ItineraryLegRef,
    SelectedItineraryBlock,
    SelectedItineraryPlan,
    TrainOption,
    TrainResultsBlock,
)
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

PRESENT_ITINERARY_TOOL_NAME = "present_itinerary"
_MAX_PLANS = 3
_MAX_LEGS = 6


class _PlanArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    reason: str = Field(min_length=1, max_length=300)
    option_ids: list[str] = Field(min_length=1, max_length=_MAX_LEGS)


class _PresentItineraryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    plans: list[_PlanArgs] = Field(min_length=1, max_length=_MAX_PLANS)


def build_present_itinerary_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": PRESENT_ITINERARY_TOOL_NAME,
            "description": render_runtime_prompt("itinerary.present_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 80},
                    "plans": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": _MAX_PLANS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "minLength": 1, "maxLength": 60},
                                "reason": {"type": "string", "minLength": 1, "maxLength": 300},
                                "option_ids": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": _MAX_LEGS,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 80},
                                },
                            },
                            "required": ["title", "reason", "option_ids"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "plans"],
                "additionalProperties": False,
            },
        },
    }


class PresentItineraryHandler(BaseToolHandler):
    # 纯本地校验，失败原因要回给模型修正，不自动重试。
    supports_automatic_retry = False
    # 失败只说明引用不对，不提示换用其他工具。
    suggests_alternatives_on_failure = False

    @property
    def tool_name(self) -> str:
        return PRESENT_ITINERARY_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "itinerary"

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        content_blocks = context.content_blocks if context is not None else ()
        if any(isinstance(block, SelectedItineraryBlock) for block in content_blocks):
            return _failed("already_presented")
        try:
            parsed = _PresentItineraryArgs.model_validate(args)
        except ValidationError:
            return _failed("invalid_arguments")

        options = _index_travel_options(content_blocks)
        unknown = [option_id for plan in parsed.plans for option_id in plan.option_ids if option_id not in options]
        if unknown:
            return _failed("unknown_option_ids", option_ids=list(dict.fromkeys(unknown)))

        plans: list[dict[str, Any]] = []
        for index, plan in enumerate(parsed.plans, start=1):
            if len(plan.option_ids) != len(set(plan.option_ids)):
                return _failed("duplicate_option_ids", plan_title=plan.title)
            legs = [options[option_id] for option_id in plan.option_ids]
            if any(
                previous[1].arrival.scheduled_at > current[1].departure.scheduled_at
                for previous, current in zip(legs, legs[1:], strict=False)
            ):
                return _failed("legs_out_of_order", plan_title=plan.title)
            plans.append(
                {
                    "id": f"plan_{index}",
                    "title": plan.title.strip(),
                    "reason": plan.reason.strip(),
                    "legs": [
                        {"block_id": block_id, "option_id": option_id}
                        for option_id, (block_id, _) in zip(plan.option_ids, legs, strict=True)
                    ],
                }
            )
        return ToolResult(status="success", data={"title": parsed.title.strip(), "plans": plans})

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> SelectedItineraryBlock | None:
        if result.status != "success":
            return None
        return SelectedItineraryBlock(
            type="itinerary_results",
            id=block_id,
            schema_version=2,
            provider="fusion",
            title=result.data["title"],
            plans=[
                SelectedItineraryPlan(
                    id=plan["id"],
                    title=plan["title"],
                    reason=plan["reason"],
                    legs=[ItineraryLegRef(**leg) for leg in plan["legs"]],
                )
                for plan in result.data["plans"]
            ],
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status != "success":
            error_code = data.get("error_code") or "invalid_arguments"
            detail = render_runtime_prompt(
                f"itinerary.failed_{error_code}",
                option_ids=", ".join(data.get("option_ids") or []),
                plan_title=data.get("plan_title") or "",
            )
            return render_runtime_prompt("itinerary.failed_context", detail=detail)
        plans = data.get("plans") or []
        return render_runtime_prompt(
            "itinerary.presented_context",
            plan_count=len(plans),
            plan_titles="; ".join(plan["title"] for plan in plans),
        )

    def _build_result_summary(self, result: ToolResult) -> dict:
        plans = (result.data or {}).get("plans") or []
        if result.status != "success":
            plans = []
        return {"kind": "itinerary", "plan_count": len(plans), "truncated": False}


def _index_travel_options(content_blocks: tuple[Any, ...]) -> dict[str, tuple[str, FlightOption | TrainOption]]:
    """option_id → (结果块 id, 班次)；同一班次被重复查询时引用最近一次结果。"""

    options: dict[str, tuple[str, FlightOption | TrainOption]] = {}
    for block in content_blocks:
        if isinstance(block, FlightResultsBlock):
            items: list[FlightOption] | list[TrainOption] = block.flights
        elif isinstance(block, TrainResultsBlock):
            items = block.trains
        else:
            continue
        for item in items:
            options[item.option_id] = (block.id, item)
    return options


def _failed(error_code: str, **details: Any) -> ToolResult:
    return ToolResult(
        status="failed",
        data={"error_code": error_code, **details},
        error_message=f"行程卡片未展示: {error_code}",
    )
