"""模型可见 system 段落的稳定内部身份。"""

APP_IDENTITY = "app_identity"
CURRENT_DATE = "current_date"
USER_PREFERENCES = "user_preferences"
TOOL_USAGE_CONTRACT = "tool_usage_contract"
AGENT_PLAN_CONTROL = "agent_plan_control"
DEEP_RESEARCH_CONTRACT = "deep_research_contract"
NO_TOOL_NETWORK_BOUNDARY = "no_tool_network_boundary"
NO_VISION_FILE_BOUNDARY = "no_vision_file_boundary"
VISIBLE_RESPONSE_LANGUAGE = "visible_response_language"
KNOWLEDGE_GROUNDING = "knowledge_grounding"
CONTINUATION_SYSTEM = "continuation_system"

PLAN_REQUIRED_REPAIR = "plan_required_repair"
PLAN_EXECUTION_REPAIR = "plan_execution_repair"
RESEARCH_COMPLETION_REPAIR = "research_completion_repair"
DEEP_RESEARCH_STAGE = "deep_research_stage"
RESEARCH_EVIDENCE_WORKSET = "research_evidence_workset"
PRODUCT_RESULT_ROUND = "product_result_round"

PLAN_SYNTHESIS = "plan_synthesis"
NO_PROGRESS_SUMMARY = "no_progress_summary"
PLAN_REPAIR_SUMMARY = "plan_repair_summary"
RESEARCH_EVIDENCE_SUMMARY = "research_evidence_summary"
LIMIT_SUMMARY = "limit_summary"
SUMMARY_TOOL_PROTOCOL_RETRY = "summary_tool_protocol_retry"

TERMINAL_CONTROL_SECTION_IDS = frozenset(
    {
        TOOL_USAGE_CONTRACT,
        AGENT_PLAN_CONTROL,
        PLAN_REQUIRED_REPAIR,
        PLAN_EXECUTION_REPAIR,
    }
)

DEEP_RESEARCH_CONTROL_SECTION_IDS = frozenset(
    {
        DEEP_RESEARCH_CONTRACT,
        DEEP_RESEARCH_STAGE,
        RESEARCH_COMPLETION_REPAIR,
    }
)


def is_terminal_control_section(section_id: str | None) -> bool:
    return bool(
        section_id in TERMINAL_CONTROL_SECTION_IDS or (isinstance(section_id, str) and section_id.startswith("skill:"))
    )
