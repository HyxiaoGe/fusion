"""从准备、续跑身份到收尾读取同一份不可变来源。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.prompts.agent_loop import get_limit_summary_prompt
from app.ai.prompts.section_ids import CONTINUATION_SYSTEM
from app.core import prompt_bundle
from app.services.runtime_config_defaults import DEFAULT_PROMPT_TEMPLATES
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config, prepare_agent_loop_messages
from test.test_prompt_bundle_snapshot import bundle_payload


@pytest.mark.anyio
async def test_continuation_and_summary_consume_same_frozen_bundle_after_active_switch():
    with (
        patch.object(prompt_bundle.settings, "PROMPTHUB_SYNC_MODE", "apply"),
        patch.object(prompt_bundle.settings, "PROMPT_P0_BASELINE_ATTESTED", True),
        patch.object(prompt_bundle, "_load_active_bundle_payload", return_value=bundle_payload("a" * 64, " 版本 A")),
    ):
        frozen = prompt_bundle.freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities={},
            original_message="继续",
            prompt_bundle_snapshot=frozen,
        )
    with patch.object(prompt_bundle, "_load_active_bundle_payload", side_effect=AssertionError("不能重新取 active")):
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
        snapshot = prepared.run_prompt_snapshot
        assert snapshot.bundle_snapshot.effective_revision == "a" * 64
        assert snapshot.fingerprint == prepared.prompt_snapshot["fingerprint"]
        with prompt_bundle.use_prompt_snapshot(snapshot):
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
