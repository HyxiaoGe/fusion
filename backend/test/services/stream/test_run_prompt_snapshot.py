"""准备、续跑与收尾读取同一份不可变本地 Prompt。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.prompts.agent_loop import get_limit_summary_prompt
from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
from app.ai.prompts.section_ids import CONTINUATION_SYSTEM
from app.core import prompt_bundle
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config, prepare_agent_loop_messages


@pytest.mark.anyio
async def test_continuation_and_summary_consume_same_frozen_local_prompts():
    defaults = {key: value + " 版本 A" for key, value in DEFAULT_PROMPT_TEMPLATES.items()}
    frozen = prompt_bundle.freeze_prompt_bundle(defaults)
    config = build_agent_loop_call_config(
        provider="openai",
        options={},
        capabilities={},
        original_message="继续",
        prompt_bundle_snapshot=frozen,
    )
    prepared = await prepare_agent_loop_messages(
        db=None,
        user_id="u1",
        raw_messages=[],
        has_vision=False,
        file_ids=[],
        original_message="继续",
        call_config=config,
        extra_system_prompts=[CONTINUATION_SYSTEM],
        preprocess_user_input=False,
        file_repo_factory=lambda db: SimpleNamespace(),
        load_user_system_prompt_fn=lambda db, user: None,
        build_llm_messages_fn=AsyncMock(return_value=[{"role": "user", "content": "继续"}]),
    )

    continuation = next(message for message in prepared.messages if message.section_id == CONTINUATION_SYSTEM)
    assert continuation.content.endswith(" 版本 A")
    assert prepared.run_prompt_snapshot.bundle_snapshot.effective_revision == frozen.effective_revision
    assert prepared.run_prompt_snapshot.fingerprint == prepared.prompt_snapshot["fingerprint"]
    with prompt_bundle.use_prompt_snapshot(prepared.run_prompt_snapshot):
        assert get_limit_summary_prompt().endswith(" 版本 A")


@pytest.mark.anyio
async def test_unknown_extra_section_is_rejected_instead_of_treating_body_as_identity():
    config = build_agent_loop_call_config(provider="openai", options={}, capabilities={})
    with pytest.raises(ValueError, match="section"):
        await prepare_agent_loop_messages(
            db=None,
            user_id="u1",
            raw_messages=[],
            has_vision=False,
            file_ids=[],
            original_message="继续",
            call_config=config,
            extra_system_prompts=["调用方预先解析的正文"],
            preprocess_user_input=False,
            file_repo_factory=lambda db: SimpleNamespace(),
            load_user_system_prompt_fn=lambda db, user: None,
            build_llm_messages_fn=AsyncMock(return_value=[]),
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"
