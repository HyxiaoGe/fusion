"""
LLM Tool 定义 — web_search / url_read

WEB_SEARCH_TOOL 是函数（不是 const），每次调用时重算当前年份，避免硬编码
过期。message_builder 同时注入"当前日期"system prompt 双重约束。
"""

from datetime import datetime, timedelta, timezone

from app.ai.prompts.agent_loop import get_url_read_tool_description
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt

_CHINA_TZ = timezone(timedelta(hours=8))


def build_web_search_tool() -> dict:
    """运行时构造 web_search tool definition，当前年份动态注入到 description 里。"""
    now = datetime.now(_CHINA_TZ)
    year = now.year
    month = now.month
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": render_runtime_prompt("ai_tools.web_search_description", year=year),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": render_runtime_prompt(
                            "ai_tools.web_search_query",
                            year=year,
                            previous_year=year - 1,
                            month=f"{month:02d}",
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                        "description": render_runtime_prompt("ai_tools.web_search_count"),
                    },
                    "intent": {
                        "type": "string",
                        "enum": [
                            "quick_fact",
                            "freshness",
                            "comparison",
                            "deep_research",
                            "official_source",
                        ],
                        "description": render_runtime_prompt("ai_tools.web_search_intent"),
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": render_runtime_prompt("ai_tools.web_search_domains"),
                    },
                    "recency_days": {
                        "type": "integer",
                        "description": render_runtime_prompt("ai_tools.web_search_recency"),
                    },
                },
                "required": ["query"],
            },
        },
    }


# 向后兼容：保留旧的常量名（同名 alias），第一次 import 时实例化。
# 严格意义上不是动态的（启动后年份固定），但启动重启频繁，约等于 dynamic。
# 推荐新代码用 build_web_search_tool()。
WEB_SEARCH_TOOL = build_web_search_tool()


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
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    }


URL_READ_TOOL = build_url_read_tool()
