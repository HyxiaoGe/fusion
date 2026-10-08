import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.chat import ContentFilteredBlock, Message, TextBlock, Usage
from app.services.chat.message_builder import build_llm_messages
from app.services.stream import llm_stream as llm_stream_module
from app.services.stream.agent_loop_outcome import AgentLoopExit
from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest, handle_agent_round_outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult, run_agent_round
from app.services.stream.provider_content_filter import (
    CONTENT_FILTER_FINISH_REASON,
    is_content_filter_error,
    is_refusal_reply,
)
from app.services.stream.step_lifecycle import AgentStepContext
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context
from test.services.stream.test_llm_stream import async_response, make_chunk

MIMO_REFUSAL = "The request was rejected because it was considered high risk"


class _ProviderError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ContentPolicyViolationError(Exception):
    pass


class SignalTests(unittest.TestCase):
    def test_refusal_reply_requires_exact_whole_content(self):
        self.assertTrue(is_refusal_reply(content=f"{MIMO_REFUSAL}\n", reasoning="", tool_calls=[]))
        self.assertFalse(
            is_refusal_reply(content=f"据报道，{MIMO_REFUSAL}", reasoning="", tool_calls=[]),
            "正文里引用这句话不算拦截",
        )
        self.assertFalse(is_refusal_reply(content=MIMO_REFUSAL, reasoning="思考过", tool_calls=[]))
        self.assertFalse(is_refusal_reply(content=MIMO_REFUSAL, reasoning="", tool_calls=[{"id": "c"}]))

    def test_content_filter_error_uses_provider_codes_only(self):
        dashscope = _ProviderError(
            "litellm.BadRequestError: OpenAIException - <400> InternalError.Algo.DataInspectionFailed: "
            "Input text data may contain inappropriate content.",
            400,
        )
        self.assertTrue(is_content_filter_error(dashscope))
        self.assertTrue(is_content_filter_error(ContentPolicyViolationError("blocked")))
        self.assertFalse(is_content_filter_error(_ProviderError("Invalid parameter: tools", 400)))
        self.assertFalse(is_content_filter_error(_ProviderError("DataInspectionFailed", 500)))
        self.assertFalse(is_content_filter_error(RuntimeError("DataInspectionFailed")))


class StreamSignalTests(unittest.IsolatedAsyncioTestCase):
    async def _consume(self, chunks):
        request = llm_stream_module.LLMStreamRequest(
            conversation_id="conv-filter",
            task_id="task-filter",
            should_use_reasoning=False,
            thinking_block_id="blk-thinking",
            text_block_id="blk-text",
        )
        with (
            patch("app.services.stream.llm_stream.append_chunk", AsyncMock()),
            patch("app.services.stream.llm_stream.check_lock_owner", AsyncMock(return_value=True)),
        ):
            return await llm_stream_module.consume_stream_round(async_response(chunks), request)

    async def test_mid_answer_content_filter_finish_reason_is_kept(self):
        outcome = await self._consume(
            [
                make_chunk(delta=SimpleNamespace(content="回答到一半"), finish_reason=None),
                make_chunk(delta=SimpleNamespace(content=""), finish_reason="content_filter"),
            ]
        )
        self.assertEqual(outcome.finish_reason, CONTENT_FILTER_FINISH_REASON)

    async def test_provider_refusal_reply_becomes_content_filter(self):
        outcome = await self._consume([make_chunk(delta=SimpleNamespace(content=MIMO_REFUSAL), finish_reason="stop")])
        self.assertEqual(outcome.finish_reason, CONTENT_FILTER_FINISH_REASON)

    async def test_normal_answer_stays_stop(self):
        outcome = await self._consume([make_chunk(delta=SimpleNamespace(content="今天晴"), finish_reason="stop")])
        self.assertEqual(outcome.finish_reason, "stop")


class AgentRoundErrorTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, error):
        observation = MagicMock()
        observation.finish_success = AsyncMock()
        observation.finish_error = AsyncMock()
        observation.wrap_response.side_effect = lambda response: response
        context_plan = MagicMock(messages=[], estimated_tokens_after=10)
        context_plan.telemetry.return_value = {"context_management_status": "no_op"}

        async def stream_round_fn(*_args, **_kwargs):
            raise error

        with (
            patch("app.services.stream.agent_round.prepare_context", new=AsyncMock(return_value=context_plan)),
            patch("app.services.stream.agent_round.create_llm_round_observation", return_value=observation),
        ):
            return await run_agent_round(
                conversation_id="conv-filter",
                task_id="task-filter",
                run_id="run-filter",
                step_number=1,
                model_id="qwen3.8-flash",
                provider="dashscope",
                litellm_model="openai/qwen3.8-flash",
                litellm_kwargs={},
                messages=[],
                should_use_reasoning=False,
                call_kwargs={},
                accumulated_usage=Usage(input_tokens=5, output_tokens=1),
                step_context=AgentStepContext("step-filter", 1, 0.0, "thinking", "text"),
                llm_call_fn=AsyncMock(return_value="response"),
                stream_round_fn=stream_round_fn,
                log_round_summary_fn=lambda **_kwargs: None,
                emitter=AsyncMock(),
            )

    async def test_provider_moderation_error_becomes_content_filter_round(self):
        result = await self._run(_ProviderError("InternalError.Algo.DataInspectionFailed", 400))

        self.assertEqual(result.finish_reason, CONTENT_FILTER_FINISH_REASON)
        self.assertEqual(result.content_buf, "")
        self.assertEqual(result.accumulated_usage.input_tokens, 5)

    async def test_other_provider_errors_still_fail_the_round(self):
        with self.assertRaisesRegex(_ProviderError, "Invalid parameter"):
            await self._run(_ProviderError("Invalid parameter", 400))


class RoundOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_filter_replaces_whole_reply_and_drops_tool_transcript(self):
        emitter = AsyncMock()
        warnings: list[str] = []
        state = AgentLoopState()
        state.content_blocks.extend([{"type": "search", "status": "success"}, {"type": "text", "text": "半截"}])
        state.tool_transcript = [{"role": "tool", "content": "搜索结果"}]
        lifecycle = MagicMock(finish_success=AsyncMock())
        request = AgentRoundOutcomeRequest(
            db=None,
            messages=[],
            state=state,
            runtime=_runtime(emitter=emitter, complete_step_fn=AsyncMock(), warning_fn=warnings.append),
            step_number=2,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="半截",
                tool_calls=[],
                finish_reason=CONTENT_FILTER_FINISH_REASON,
                accumulated_usage=Usage(),
                llm_lifecycle=lifecycle,
            ),
        )

        outcome = await handle_agent_round_outcome(request=request)

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(len(state.content_blocks), 1)
        self.assertEqual(state.content_blocks[0].type, "content_filtered")
        self.assertEqual(state.tool_transcript, [])
        emitter.content_block_upserted.assert_awaited_once()
        lifecycle.record_output.assert_called_once_with(
            disposition="replaced", source="server", reason="content_filtered"
        )
        self.assertTrue(any("run-outcome" in warning for warning in warnings))


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtered_turn_is_left_out_of_later_context(self):
        messages = [
            Message(role="user", content=[TextBlock(type="text", text="第一问")]),
            Message(role="assistant", content=[TextBlock(type="text", text="第一答")]),
            Message(role="user", content=[TextBlock(type="text", text="被拦的问题")]),
            Message(role="assistant", content=[ContentFilteredBlock(type="content_filtered", schema_version=1)]),
            Message(role="user", content=[TextBlock(type="text", text="换个话题")]),
        ]

        result = await build_llm_messages(messages, has_vision=False, file_repo=None, include_base_system=False)

        contents = [message["content"] for message in result if message["role"] != "system"]
        self.assertEqual(contents, ["第一问", "第一答", "换个话题"])


if __name__ == "__main__":
    unittest.main()
