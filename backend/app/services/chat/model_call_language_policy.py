"""真实模型调用前的可见输出语言策略。"""

from __future__ import annotations

from app.ai.prompts.agent_loop import VISIBLE_RESPONSE_LANGUAGE_PROMPT
from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages
from app.ai.prompts.section_ids import VISIBLE_RESPONSE_LANGUAGE


def finalize_model_call_language_policy(
    messages: list[PromptMessage | dict],
) -> list[PromptMessage]:
    """按稳定身份在最后一条有效 system 指令后保留唯一语言契约。"""

    finalized = [
        message for message in ensure_prompt_messages(messages) if message.section_id != VISIBLE_RESPONSE_LANGUAGE
    ]
    language_message = PromptMessage(
        role="system",
        content=VISIBLE_RESPONSE_LANGUAGE_PROMPT,
        section_id=VISIBLE_RESPONSE_LANGUAGE,
    )

    last_system_index = next(
        (index for index in range(len(finalized) - 1, -1, -1) if finalized[index].role == "system"),
        None,
    )
    if last_system_index is None:
        return [language_message, *finalized]

    finalized.insert(last_system_index + 1, language_message)
    return finalized
