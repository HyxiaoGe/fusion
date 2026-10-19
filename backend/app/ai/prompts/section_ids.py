"""模型可见 system 段落的稳定内部身份。"""

APP_IDENTITY = "app_identity"
CURRENT_DATE = "current_date"
USER_PREFERENCES = "user_preferences"
USER_MEMORY = "user_memory"
TOOL_USAGE_CONTRACT = "tool_usage_contract"
TOOL_SELECTION_POLICY = "tool_selection_policy"
NO_TOOL_NETWORK_BOUNDARY = "no_tool_network_boundary"
NO_VISION_FILE_BOUNDARY = "no_vision_file_boundary"
VISIBLE_RESPONSE_LANGUAGE = "visible_response_language"
CONTINUATION_SYSTEM = "continuation_system"
DOCUMENT_OUTPUT_CONTRACT = "document_output_contract"
CURRENT_DOCUMENTS = "current_documents"
SKILLS_CATALOG = "skills_catalog"

RESEARCH_EVIDENCE_WORKSET = "research_evidence_workset"

LIMIT_SUMMARY = "limit_summary"
SUMMARY_TOOL_PROTOCOL_RETRY = "summary_tool_protocol_retry"

TERMINAL_CONTROL_SECTION_IDS = frozenset(
    {
        TOOL_USAGE_CONTRACT,
        TOOL_SELECTION_POLICY,
        DOCUMENT_OUTPUT_CONTRACT,
        SKILLS_CATALOG,
    }
)


def is_terminal_control_section(section_id: str | None) -> bool:
    return section_id in TERMINAL_CONTROL_SECTION_IDS
