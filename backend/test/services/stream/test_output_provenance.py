"""正文归因只在生产发布边界决定，候选内容保持独立。"""

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.chat import PlaceResult, PlaceResultsBlock, Usage
from app.services.agent.emitter import AgentEventEmitter
from app.services.agent.trajectory_payload import build_trajectory_payload
from app.services.stream.agent_loop_round_outcome import AgentRoundOutcomeRequest, handle_agent_round_outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.limit_summary import LimitSummaryRoundResult, _commit_limit_summary_result
from app.services.stream.llm_round_lifecycle import LLMRoundLifecycle
from test.services.stream import test_limit_summary as summary_tests
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class OutputProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def _lifecycle(self):
        return await LLMRoundLifecycle.start(
            emitter=AsyncMock(),
            observation=MagicMock(duration_ms=20, output_delta_ms=lambda _: 2),
            round_index=1,
            model="test",
            provider="test",
            parent_step_id="step-outcome",
            text_block_id="step-outcome-text",
        )

    async def test_thinking_before_content_preserves_actual_body_on_all_terminals(self):
        for terminal in ("success", "failed", "cancelled"):
            with self.subTest(terminal=terminal):
                lifecycle = await self._lifecycle()
                await lifecycle.publish_visible_output("reasoning")
                await lifecycle.publish_visible_output("content")
                if terminal == "success":
                    await lifecycle.finish_success()
                    event = lifecycle.emitter.llm_round_completed
                elif terminal == "failed":
                    await lifecycle.finish_failed(RuntimeError("secret"))
                    event = lifecycle.emitter.llm_round_failed
                else:
                    await lifecycle.finish_cancelled(reason="shutdown")
                    event = lifecycle.emitter.llm_round_cancelled
                provenance = event.call_args.kwargs["output_provenance"]
                self.assertEqual(provenance["disposition"], "emitted")
                self.assertEqual(provenance["source"], "model")
                self.assertEqual(provenance["block_id"], "step-outcome-text")

    async def test_deferred_adoption_knowledge_and_plan_suppression(self):
        for mode, disposition, source, reason in (
            ("adopt", "emitted", "model", "deferred"),
            ("knowledge", "replaced", "server", "knowledge_guard"),
            ("plan", "suppressed", "none", "plan_continues"),
        ):
            with self.subTest(mode=mode):
                lifecycle = await self._lifecycle()
                lifecycle.record_detail(reasoning_text="", content_text="原始模型候选")
                runtime = _runtime(
                    emitter=lifecycle.emitter,
                    evidence_policy="knowledge_grounded_v1" if mode == "knowledge" else None,
                    plan_mode="on" if mode == "plan" else "off",
                )
                request = AgentRoundOutcomeRequest(
                    db=None,
                    messages=[],
                    state=AgentLoopState(),
                    runtime=runtime,
                    step_number=1,
                    step_context=_step_context(),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="原始模型候选",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(),
                        output_deferred=True,
                        llm_lifecycle=lifecycle,
                    ),
                )
                with (
                    patch("app.services.stream.agent_loop_round_outcome.append_chunk", new=AsyncMock()) as append,
                    patch("app.services.stream.agent_loop_round_outcome.complete_text_response_step", new=AsyncMock()),
                ):
                    await handle_agent_round_outcome(request=request)
                provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
                self.assertEqual(
                    provenance,
                    {
                        "disposition": disposition,
                        "source": source,
                        "reason": reason,
                        "block_id": None if mode == "plan" else "step-outcome-text",
                    },
                )
                self.assertEqual(lifecycle.content_text, "原始模型候选")
                self.assertEqual(append.await_count, 0 if mode == "plan" else 1)

    async def test_commit_failure_never_claims_unpublished_answer(self):
        lifecycle = await self._lifecycle()
        request = AgentRoundOutcomeRequest(
            db=None,
            messages=[],
            state=AgentLoopState(),
            runtime=_runtime(),
            step_number=1,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="候选",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(),
                output_deferred=True,
                llm_lifecycle=lifecycle,
            ),
        )
        error = asyncio.CancelledError()
        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", new=AsyncMock(side_effect=error)):
            with self.assertRaises(asyncio.CancelledError):
                await handle_agent_round_outcome(request=request)
        provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
        self.assertEqual(provenance["disposition"], "suppressed")
        self.assertIsNone(provenance["block_id"])

    def test_ledger_keeps_only_bounded_provenance_without_candidate_or_answer(self):
        provenance = {"disposition": "replaced", "source": "server", "reason": "product_guard", "block_id": "text-1"}
        result = build_trajectory_payload(
            {
                "type": "llm_round_completed",
                "llm_round_id": "round-1",
                "output_provenance": {**provenance, "candidate": "不可复制", "answer": "不可复制"},
            }
        )
        self.assertEqual(result.get("output_provenance"), provenance)
        self.assertNotIn("不可复制", str(result))

    async def test_actual_emitter_terminal_envelope_and_safe_ledger(self):
        captured = []

        class Writer:
            async def append_chunk(self, _conversation, _task, _kind, payload):
                captured.append(payload)

        emitter = AgentEventEmitter(
            run_id="run-exact",
            trace_id="trace-exact",
            conversation_id="conv-exact",
            task_id="task-exact",
            redis_writer=Writer(),
        )
        lifecycle = await LLMRoundLifecycle.start(
            emitter=emitter,
            observation=MagicMock(duration_ms=10, output_delta_ms=lambda _: 1),
            round_index=1,
            model="model",
            provider="provider",
            parent_step_id="step-exact",
            text_block_id="text-exact",
        )
        lifecycle.record_detail(reasoning_text="", content_text="完整候选不可复制")
        await lifecycle.publish_visible_output("content")
        await lifecycle.finish_success()
        terminal = build_trajectory_payload(captured[-1])
        self.assertEqual(terminal["run_id"], "run-exact")
        self.assertEqual(terminal["parent_step_id"], "step-exact")
        self.assertEqual(terminal["llm_round_id"], lifecycle.llm_round_id)
        self.assertEqual(terminal["output_provenance"]["block_id"], "text-exact")
        self.assertNotIn("完整候选", str(captured))

    async def test_product_brand_preservation_and_tool_sanitization_record_actual_output_source(self):
        for candidate, expected, disposition, source in (
            (
                "根据高德返回的结果，可以优先查看高德置地广场。",
                "根据高德返回的结果，可以优先查看高德置地广场。",
                "emitted",
                "model",
            ),
            (
                "local_place_search 返回的候选地点是高德置地广场。",
                "地点搜索返回的候选地点是高德置地广场。",
                "replaced",
                "server",
            ),
        ):
            with self.subTest(candidate=candidate):
                lifecycle = await self._lifecycle()
                lifecycle.record_detail(reasoning_text="", content_text=candidate)
                state = AgentLoopState(product_tool_attempted=True)
                state.mark_current_step("step-outcome")
                state.content_blocks.append(
                    PlaceResultsBlock(
                        type="place_results",
                        schema_version=1,
                        provider="amap",
                        query="商场",
                        status="success",
                        result_count=1,
                        places=[PlaceResult(name="高德置地广场")],
                    )
                )
                request = AgentRoundOutcomeRequest(
                    db=None,
                    messages=[{"role": "user", "content": "附近有什么商场"}],
                    state=state,
                    runtime=_runtime(emitter=lifecycle.emitter),
                    step_number=1,
                    step_context=_step_context(),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf=candidate,
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(),
                        output_deferred=True,
                        llm_lifecycle=lifecycle,
                    ),
                )
                with (
                    patch("app.services.stream.agent_loop_round_outcome.append_chunk", new=AsyncMock()) as append,
                    patch("app.services.stream.agent_loop_round_outcome.complete_text_response_step", new=AsyncMock()),
                ):
                    await handle_agent_round_outcome(request=request)
                self.assertEqual(append.call_args.args[2], expected)
                self.assertEqual(state.content_blocks[-1].text, expected)
                provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
                self.assertEqual(provenance["disposition"], disposition)
                self.assertEqual(provenance["source"], source)
                self.assertEqual(lifecycle.content_text, candidate)

    async def test_product_repair_and_fallback_are_server_output(self):
        for mode in ("repair", "fallback", "pending"):
            with self.subTest(mode=mode):
                lifecycle = await self._lifecycle()
                lifecycle.record_detail(reasoning_text="", content_text="模型候选")
                request = AgentRoundOutcomeRequest(
                    db=None,
                    messages=[],
                    state=AgentLoopState(product_tool_attempted=True),
                    runtime=_runtime(),
                    step_number=1,
                    step_context=_step_context(),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="模型候选",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=Usage(),
                        output_deferred=True,
                        llm_lifecycle=lifecycle,
                    ),
                )
                prefix = "app.services.stream.agent_loop_round_outcome."
                with (
                    patch(prefix + "append_chunk", new=AsyncMock()) as append,
                    patch(prefix + "complete_text_response_step", new=AsyncMock()),
                    patch(
                        prefix + "build_tool_repair_clarification", return_value="待补参数" if mode == "pending" else ""
                    ),
                    patch(
                        prefix + "validate_product_answer",
                        return_value=SimpleNamespace(is_valid=False, reason_code="test"),
                    ),
                    patch(
                        prefix + "repair_unsupported_product_answer",
                        return_value=("修整回答", "test") if mode == "repair" else (None, "test"),
                    ),
                    patch(prefix + "build_grounded_product_answer", return_value="确定性回答"),
                    patch(prefix + "_emit_product_answer_observation"),
                    patch(prefix + "settings.PRODUCT_ANSWER_REPAIR_ENABLED", True),
                ):
                    await handle_agent_round_outcome(request=request)
                provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
                self.assertEqual(provenance["disposition"], "replaced")
                self.assertEqual(provenance["source"], "server")
                self.assertEqual(provenance["block_id"], append.call_args.args[3])
                self.assertEqual(lifecycle.content_text, "模型候选")

    async def test_tool_retraction_finishes_model_before_tool_execution(self):
        lifecycle = await self._lifecycle()
        await lifecycle.publish_visible_output("content")
        runtime = _runtime(emitter=lifecycle.emitter, handle_tool_calls_round_fn=AsyncMock())

        async def run_tools(**_kwargs):
            provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
            self.assertEqual(provenance["reason"], "tool_retracted")
            self.assertEqual(provenance["disposition"], "suppressed")
            lifecycle.emitter.content_block_discarded.assert_awaited_once()

        runtime.handle_tool_calls_round_fn.side_effect = run_tools
        request = AgentRoundOutcomeRequest(
            db=None,
            messages=[],
            state=AgentLoopState(),
            runtime=runtime,
            step_number=1,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="已流式展示",
                tool_calls=[{"name": "web_search", "id": "tool-1"}],
                finish_reason="tool_calls",
                accumulated_usage=Usage(),
                llm_lifecycle=lifecycle,
            ),
        )
        await handle_agent_round_outcome(request=request)
        runtime.handle_tool_calls_round_fn.assert_awaited_once()
        lifecycle.emitter.llm_round_completed.assert_awaited_once()

    async def test_summary_adoption_and_guard_replacement_compare_original_candidate(self):
        for mode in ("adopt", "knowledge", "guard", "research"):
            with self.subTest(mode=mode):
                lifecycle = await self._lifecycle()
                lifecycle.record_detail(reasoning_text="", content_text="模型总结候选")
                request = replace(
                    summary_tests.LimitSummaryStepTests._deferred_commit_request(),
                    evidence_policy="knowledge_grounded_v1" if mode == "knowledge" else None,
                    task_mode="deep_research" if mode == "research" else "normal",
                )
                result = LimitSummaryRoundResult(
                    reasoning_buf="", content_buf="模型总结候选", usage_data=None, llm_lifecycle=lifecycle
                )
                with (
                    patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append,
                    patch("app.services.stream.limit_summary._emit_knowledge_summary_used_evidence", new=AsyncMock()),
                    patch(
                        "app.services.stream.limit_summary._guard_no_evidence_answer",
                        return_value=("服务端总结", "test") if mode == "guard" else ("模型总结候选", None),
                    ),
                ):
                    await _commit_limit_summary_result(
                        request=request,
                        round_result=result,
                        summary_context=_step_context(),
                        thinking_block_id="thinking",
                        text_block_id="step-outcome-text",
                    )
                    await lifecycle.finish_success()
                provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
                self.assertEqual(provenance["disposition"], "emitted" if mode == "adopt" else "replaced")
                self.assertEqual(provenance["source"], "model" if mode == "adopt" else "server")
                self.assertEqual(provenance["block_id"], append.call_args.args[3])
                self.assertEqual(lifecycle.content_text, "模型总结候选")

    async def test_plan_synthesis_protocol_fallback_is_not_reported_as_model_output(self):
        """零证据综合被协议兜底替换后，终态不能记成模型正文已送达（PR #72 复审）。"""

        lifecycle = await self._lifecycle()
        lifecycle.record_detail(reasoning_text="", content_text="残缺的工具协议")
        request = replace(
            summary_tests.LimitSummaryStepTests._deferred_commit_request(),
            summary_finish_reason="plan_synthesis",
            task_mode="normal",
        )
        result = LimitSummaryRoundResult(
            reasoning_buf="",
            content_buf="残缺的工具协议",
            usage_data=None,
            llm_lifecycle=lifecycle,
            finish_reason="protocol_fallback",
        )
        with (
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
            patch(
                "app.services.stream.limit_summary._safe_summary_fallback",
                new=AsyncMock(return_value="服务端兜底文案"),
            ),
            patch(
                "app.services.stream.limit_summary._guard_no_evidence_answer",
                new=AsyncMock(return_value=("服务端兜底文案", None)),
            ),
        ):
            await _commit_limit_summary_result(
                request=request,
                round_result=result,
                summary_context=_step_context(),
                thinking_block_id="thinking",
                text_block_id="step-outcome-text",
            )
            await lifecycle.finish_success()
        # 归因账本由 _record_summary_output 写，本来就正确；这里守的是 TTFT：
        # 模型正文从未送达，不能为这一轮发出"首次输出"事件。
        provenance = lifecycle.emitter.llm_round_completed.call_args.kwargs["output_provenance"]
        self.assertEqual(provenance["disposition"], "replaced")
        self.assertEqual(provenance["source"], "server")
        lifecycle.emitter.llm_round_first_output_delta.assert_not_awaited()
        self.assertFalse(lifecycle.first_output_emitted)

    async def test_server_terminal_without_model_does_not_borrow_previous_round(self):
        emitter = AsyncMock()
        request = AgentRoundOutcomeRequest(
            db=None,
            messages=[],
            state=AgentLoopState(limit_reason="max_steps", product_tool_attempted=True),
            runtime=_runtime(emitter=emitter),
            step_number=2,
            step_context=_step_context(),
            terminal=True,
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(),
            ),
        )
        with (
            patch("app.services.stream.agent_loop_round_outcome.append_chunk", new=AsyncMock()) as append,
            patch("app.services.stream.agent_loop_round_outcome.complete_text_response_step", new=AsyncMock()),
            patch(
                "app.services.stream.agent_loop_round_outcome.build_grounded_product_answer",
                return_value="现有产品事实",
            ),
        ):
            await handle_agent_round_outcome(request=request)
        append.assert_awaited_once()
        emitter.llm_round_started.assert_not_awaited()
        emitter.llm_round_completed.assert_not_awaited()
