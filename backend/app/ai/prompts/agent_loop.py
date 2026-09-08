"""Agent loop 提示词常量。

主聊天规则以代码维护；摘要和工具说明保留现有解析路径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.ai.prompts.local_templates import CODE_DEFAULT_PROMPT_TEMPLATES
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.prompt_bundle import resolve_prompt_template
from app.core.prompt_catalog import register_prompt_consumer

CHINA_TZ = timezone(timedelta(hours=8))


def build_current_date_system_prompt(now: datetime | None = None) -> str:
    """为 LLM 注入当前真实日期，避免模型凭训练 cutoff 猜年份。"""
    current = now or datetime.now(CHINA_TZ)
    current = current.replace(tzinfo=CHINA_TZ) if current.tzinfo is None else current.astimezone(CHINA_TZ)
    tomorrow = current.date() + timedelta(days=1)
    week_start = current.date() - timedelta(days=current.weekday())
    this_saturday = week_start + timedelta(days=5)
    this_sunday = week_start + timedelta(days=6)
    next_saturday = this_saturday + timedelta(days=7)
    next_sunday = this_sunday + timedelta(days=7)
    return render_runtime_prompt(
        "agent_loop.current_date",
        current_date=_format_date_with_weekday(current.date()),
        current_time=current.strftime("%H:%M"),
        tomorrow=_format_date_with_weekday(tomorrow),
        this_saturday=_format_date_with_weekday(this_saturday),
        this_sunday=_format_date_with_weekday(this_sunday),
        next_saturday=_format_date_with_weekday(next_saturday),
        next_sunday=_format_date_with_weekday(next_sunday),
    )


def _format_date_with_weekday(value) -> str:
    weekday = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][value.weekday()]
    return f"{value.isoformat()} ({weekday})"


APP_IDENTITY_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["app_identity"]

VISIBLE_RESPONSE_LANGUAGE_PROMPT = render_runtime_prompt("agent_loop.visible_response_language")


TOOL_USAGE_CONTRACT_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["tool_usage_contract"]
NETWORK_DECISION_PROMPT = TOOL_USAGE_CONTRACT_PROMPT.partition("\n\n[Tool-call consistency]")[0]

DEEP_RESEARCH_CONTRACT_PROMPT = render_runtime_prompt("agent_loop.deep_research_contract")

AGENT_PLAN_CONTROL_AUTO_PROMPT = render_runtime_prompt("agent_loop.plan_control_auto")

AGENT_PLAN_CONTROL_ON_PROMPT = render_runtime_prompt("agent_loop.plan_control_on")

NO_TOOL_NETWORK_BOUNDARY_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["no_tool_network_boundary"]

NO_VISION_FILE_BOUNDARY_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["no_vision_file_boundary"]

SUMMARY_NON_DISCLOSURE_PROMPT = render_runtime_prompt("agent_loop.summary_non_disclosure")

PLAN_SYNTHESIS_PROMPT = render_runtime_prompt("agent_loop.plan_synthesis")

LIMIT_SUMMARY_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["limit_summary"]

NO_PROGRESS_SUMMARY_PROMPT = render_runtime_prompt("agent_loop.no_progress_summary")

NO_TOOL_EVIDENCE_SUMMARY_PROMPT = render_runtime_prompt("agent_loop.no_tool_evidence_summary")

PLAN_REPAIR_SUMMARY_PROMPT = render_runtime_prompt("agent_loop.plan_repair_summary")

RESEARCH_EVIDENCE_SUMMARY_PROMPT = render_runtime_prompt("agent_loop.research_evidence_summary")

CONTINUATION_SYSTEM_PROMPT = CODE_DEFAULT_PROMPT_TEMPLATES["continuation_system"]


SEARCH_CONTEXT_OPENING = render_runtime_prompt("agent_loop.search_context_opening")

SEARCH_CONTEXT_FOLLOW_UP_RULES = [
    render_runtime_prompt(f"agent_loop.search_context_follow_up_{index}") for index in range(1, 6)
]

URL_READ_TOOL_DESCRIPTION = CODE_DEFAULT_PROMPT_TEMPLATES["url_read_tool_description"]


def get_runtime_prompt_template(name: str, fallback: str) -> str:
    return resolve_prompt_template(name, fallback)


@register_prompt_consumer("app_identity")
def get_app_identity_prompt() -> str:
    return get_runtime_prompt_template("app_identity", APP_IDENTITY_PROMPT)


@register_prompt_consumer("tool_usage_contract")
def get_tool_usage_contract_prompt() -> str:
    return get_runtime_prompt_template("tool_usage_contract", TOOL_USAGE_CONTRACT_PROMPT)


@register_prompt_consumer("no_tool_network_boundary")
def get_no_tool_network_boundary_prompt() -> str:
    return get_runtime_prompt_template("no_tool_network_boundary", NO_TOOL_NETWORK_BOUNDARY_PROMPT)


def get_agent_plan_control_prompt(plan_mode: str) -> str:
    """计划控制正文不在 catalog 内，本期继续由代码维护。"""

    if plan_mode == "on":
        return AGENT_PLAN_CONTROL_ON_PROMPT
    return AGENT_PLAN_CONTROL_AUTO_PROMPT


@register_prompt_consumer("no_vision_file_boundary")
def get_no_vision_file_boundary_prompt() -> str:
    return get_runtime_prompt_template("no_vision_file_boundary", NO_VISION_FILE_BOUNDARY_PROMPT)


@register_prompt_consumer("url_read_tool_description")
def get_url_read_tool_description() -> str:
    return get_runtime_prompt_template("url_read_tool_description", URL_READ_TOOL_DESCRIPTION)


@register_prompt_consumer("limit_summary")
def get_limit_summary_prompt() -> str:
    return get_runtime_prompt_template("limit_summary", LIMIT_SUMMARY_PROMPT)


@register_prompt_consumer("continuation_system")
def get_continuation_system_prompt() -> str:
    return get_runtime_prompt_template("continuation_system", CONTINUATION_SYSTEM_PROMPT)
