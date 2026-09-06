"""catalog 的代码默认正文；供冻结解析与配置种子共用。"""

from app.ai.prompts.agent_loop import (
    APP_IDENTITY_PROMPT,
    CONTINUATION_SYSTEM_PROMPT,
    LIMIT_SUMMARY_PROMPT,
    NO_TOOL_NETWORK_BOUNDARY_PROMPT,
    NO_VISION_FILE_BOUNDARY_PROMPT,
    TOOL_USAGE_CONTRACT_PROMPT,
    URL_READ_TOOL_DESCRIPTION,
)
from app.ai.prompts.templates import (
    FILE_ANALYSIS_PROMPT,
    FILE_CONTENT_ENHANCEMENT_PROMPT,
    GENERATE_SUGGESTED_QUESTIONS_PROMPT,
    GENERATE_TITLE_PROMPT,
)

DEFAULT_PROMPT_TEMPLATES = {
    "app_identity": APP_IDENTITY_PROMPT,
    "tool_usage_contract": TOOL_USAGE_CONTRACT_PROMPT,
    "no_tool_network_boundary": NO_TOOL_NETWORK_BOUNDARY_PROMPT,
    "no_vision_file_boundary": NO_VISION_FILE_BOUNDARY_PROMPT,
    "url_read_tool_description": URL_READ_TOOL_DESCRIPTION,
    "limit_summary": LIMIT_SUMMARY_PROMPT,
    "continuation_system": CONTINUATION_SYSTEM_PROMPT,
    "generate_title": GENERATE_TITLE_PROMPT,
    "generate_suggested_questions": GENERATE_SUGGESTED_QUESTIONS_PROMPT,
    "file_analysis": FILE_ANALYSIS_PROMPT,
    "file_content_enhancement": FILE_CONTENT_ENHANCEMENT_PROMPT,
}
