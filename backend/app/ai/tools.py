"""
LLM Tool 定义 — url_read / image_search / video_search

web_search 的定义来自 agent-tools 搜索服务的工具目录，见 app/services/tool_handlers/web_search.py。
"""

from app.ai.prompts.agent_loop import get_url_read_tool_description
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

IMAGE_SEARCH_TOOL_NAME = "image_search"
DEFAULT_IMAGE_COUNT = 6
MAX_IMAGE_COUNT = 10
MAX_IMAGE_QUERY_CHARS = 200


def build_image_search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": IMAGE_SEARCH_TOOL_NAME,
            "description": render_runtime_prompt("ai_tools.image_search_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "maxLength": MAX_IMAGE_QUERY_CHARS,
                        "description": render_runtime_prompt("ai_tools.image_search_query"),
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_IMAGE_COUNT,
                        "default": DEFAULT_IMAGE_COUNT,
                        "description": render_runtime_prompt("ai_tools.image_search_count"),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


VIDEO_SEARCH_TOOL_NAME = "video_search"
DEFAULT_VIDEO_COUNT = 5
MAX_VIDEO_COUNT = 8
MAX_VIDEO_QUERY_CHARS = 200


def build_video_search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": VIDEO_SEARCH_TOOL_NAME,
            "description": render_runtime_prompt("ai_tools.video_search_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "maxLength": MAX_VIDEO_QUERY_CHARS,
                        "description": render_runtime_prompt("ai_tools.video_search_query"),
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_VIDEO_COUNT,
                        "default": DEFAULT_VIDEO_COUNT,
                        "description": render_runtime_prompt("ai_tools.video_search_count"),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def build_url_read_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "url_read",
            "description": get_url_read_tool_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": render_runtime_prompt("ai_tools.url"),
                    },
                    "reason": {
                        "type": "string",
                        "description": render_runtime_prompt("ai_tools.url_reason"),
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": render_runtime_prompt("ai_tools.url_full_page"),
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    }


URL_READ_TOOL = build_url_read_tool()
