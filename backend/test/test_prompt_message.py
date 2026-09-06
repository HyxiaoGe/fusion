from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest


def test_prompt_message_is_immutable_and_rejects_non_system_identity():
    from app.ai.prompts.prompt_message import PromptMessage

    message = PromptMessage(role="system", content="规则", section_id="app_identity")

    with pytest.raises(FrozenInstanceError):
        message.content = "变化"
    with pytest.raises(ValueError, match="非 system 消息不能携带 section_id"):
        PromptMessage(role="user", content="问题", section_id="app_identity")


def test_provider_conversion_strips_internal_identity_and_keeps_protocol_fields():
    from app.ai.prompts.prompt_message import PromptMessage, to_provider_messages

    messages = [
        PromptMessage(role="system", content="规则", section_id="app_identity"),
        PromptMessage.from_provider_dict(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "先查资料",
                "tool_calls": [{"id": "call-1", "function": {"name": "web_search"}}],
            }
        ),
    ]

    assert to_provider_messages(messages) == [
        {"role": "system", "content": "规则"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "先查资料",
            "tool_calls": [{"id": "call-1", "function": {"name": "web_search"}}],
        },
    ]
    assert "section_id" not in dict(messages[0])


def test_ensure_prompt_message_preserves_existing_identity_and_normalizes_provider_dict():
    from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_message

    existing = PromptMessage(role="system", content="规则", section_id="tool_usage_contract")

    assert ensure_prompt_message(existing) is existing
    normalized = ensure_prompt_message({"role": "tool", "content": "结果", "tool_call_id": "call-1"})
    assert normalized.role == "tool"
    assert normalized.section_id is None
    assert normalized.get("tool_call_id") == "call-1"


def test_deepcopy_preserves_identity_and_isolates_multimodal_content():
    from app.ai.prompts.prompt_message import PromptMessage

    message = PromptMessage(
        role="system",
        content=[{"type": "text", "text": "规则"}],
        section_id="app_identity",
    )

    copied = deepcopy(message)

    assert copied == message
    assert copied is not message
    assert copied.section_id == "app_identity"
    assert copied.content is not message.content


def test_provider_conversion_preserves_legacy_language_message_shape():
    from app.ai.prompts.prompt_message import PromptMessage, to_provider_messages

    messages = [
        PromptMessage(role="system", content="最后一条规则", section_id="deep_research_stage"),
        PromptMessage(
            role="system",
            content="语言规则",
            section_id="visible_response_language",
        ),
        PromptMessage(role="user", content="问题"),
    ]

    assert to_provider_messages(messages) == [
        {"role": "system", "content": "最后一条规则\n\n语言规则"},
        {"role": "user", "content": "问题"},
    ]
