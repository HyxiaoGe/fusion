"""
Kimi $web_search 调用服务

使用 Kimi K2.5 的内置 $web_search 工具搜索热点，
由 LLM 改写成适合向 AI 提问的示例问题。
"""

import json
from typing import Optional

from openai import AsyncOpenAI

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.config import settings
from app.core.logger import app_logger as logger

SYSTEM_PROMPT = render_runtime_prompt("kimi_search.system")
USER_PROMPT = render_runtime_prompt("kimi_search.user")


async def fetch_trending_questions() -> Optional[list[dict]]:
    """
    调用 Kimi K2.5 + $web_search 生成 10 条示例问题。

    返回 [{"category": "news|tech|general", "question": "..."}] 或 None。
    """
    if not settings.MOONSHOT_API_KEY:
        logger.warning("MOONSHOT_API_KEY 未配置，跳过示例问题生成")
        return None

    client = AsyncOpenAI(
        api_key=settings.MOONSHOT_API_KEY,
        base_url="https://api.moonshot.cn/v1",
    )

    tools = [
        {
            "type": "builtin_function",
            "function": {"name": "$web_search"},
        }
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]

    try:
        # Kimi 的 $web_search 需要多轮 tool_call 循环
        for _ in range(5):  # 最多 5 轮（通常 2-3 轮就结束）
            # 注：此路径直接用 OpenAI client 调 Moonshot API，不经过 LLMManager / proxy，
            # 没有前置层注入的 extra_body，所以直接赋值是安全的（不需要 merge_extra_body）。
            response = await client.chat.completions.create(
                model="kimi-k2.5",
                messages=messages,
                tools=tools,
                extra_body={"thinking": {"type": "disabled"}},  # K2.5 使用 $web_search 必须禁用思考
            )

            choice = response.choices[0]

            # 模型完成回答，提取 JSON
            if choice.finish_reason == "stop":
                return _parse_questions(choice.message.content)

            # 模型请求调用工具，把结果回填
            if choice.message.tool_calls:
                messages.append(choice.message.model_dump())
                for tool_call in choice.message.tool_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_call.function.arguments,
                        }
                    )
            else:
                # 无 tool_call 也没 stop，异常退出
                break

        logger.warning("Kimi 调用未在预期轮次内完成")
        return None

    except Exception as e:
        logger.error(f"Kimi $web_search 调用失败: {e}")
        return None
    finally:
        await client.close()


def _parse_questions(content: str) -> Optional[list[dict]]:
    """从 LLM 返回内容中提取 JSON 数组"""
    if not content:
        return None

    # 尝试直接解析
    try:
        data = json.loads(content)
        if isinstance(data, list) and all("question" in item and "category" in item for item in data):
            return data
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    import re

    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning(f"无法解析 Kimi 返回的问题列表: {content[:200]}")
    return None
