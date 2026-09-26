"""产品结果模型回答统一经过校验并记录真实分母。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.stream import agent_loop_round_outcome as outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.product_answer_validator import ProductAnswerValidation
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class ProductAnswerObservationPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_product_result_types_use_validated_observation_path(self):
        for block_type in ("weather_results", "flight_results", "train_results"):
            with self.subTest(block_type=block_type):
                request = outcome.AgentRoundOutcomeRequest(
                    db=None,
                    messages=[],
                    state=AgentLoopState(product_tool_attempted=True, content_blocks=[{"type": block_type}]),
                    runtime=_runtime(),
                    step_number=1,
                    step_context=_step_context(),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="模型候选",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=None,
                    ),
                )
                with (
                    patch.object(outcome, "_append_committed_answer", new=AsyncMock()) as append,
                    patch.object(outcome, "has_tool_evidence", return_value=True),
                    patch.object(
                        outcome,
                        "validate_product_answer",
                        return_value=ProductAnswerValidation(True, "ok"),
                    ) as validate,
                    patch.object(outcome, "emit_product_answer_observation") as emit,
                    patch.object(outcome, "retain_product_answer_observation", new=AsyncMock(), create=True) as retain,
                ):
                    await outcome._commit_deferred_product_answer(request)
                emit.assert_called_once()
                payload = emit.call_args.args[0]
                self.assertEqual(payload["observation_path"], "validated")
                self.assertTrue(payload["validated"])
                self.assertTrue(payload["is_valid"])
                self.assertEqual(payload["reason_code"], "ok")
                self.assertTrue(payload["product_tool_attempted"])
                retain.assert_awaited_once_with(payload)
                validate.assert_called_once()
                append.assert_awaited_once()
                self.assertNotIn("模型候选", str(payload))

    async def test_missing_usable_evidence_records_fallback_without_crashing(self):
        model_answer = "G737 参考价 598 元。"
        request = outcome.AgentRoundOutcomeRequest(
            db=None,
            messages=[{"role": "user", "content": "查询北京到上海的高铁"}],
            state=AgentLoopState(
                product_tool_attempted=True,
                content_blocks=[
                    {
                        "type": "train_results",
                        "trains": [{"train_no": "G737", "price": {"currency": "CNY", "amount_minor": 59800}}],
                    }
                ],
            ),
            runtime=_runtime(),
            step_number=1,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf=model_answer,
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=None,
            ),
        )
        with (
            patch.object(outcome, "_append_committed_answer", new=AsyncMock()) as append,
            patch.object(outcome, "emit_product_answer_observation") as emit,
            patch.object(outcome, "retain_product_answer_observation", new=AsyncMock(), create=True) as retain,
        ):
            result = await outcome._commit_deferred_product_answer(request)

        payload = emit.call_args.args[0]
        self.assertEqual(payload["observation_path"], "no_usable_evidence")
        self.assertFalse(payload["validated"])
        self.assertIsNone(payload["is_valid"])
        self.assertEqual(payload["reason_code"], "not_validated")
        self.assertEqual(payload["product_result_types"], ["train_results"])
        self.assertNotIn(model_answer, str(payload))
        retain.assert_awaited_once_with(payload)
        append.assert_awaited_once()
        fallback = append.await_args.args[1]
        self.assertIn("没能完成所需的查询", fallback)
        self.assertEqual(result.round_result.content_buf, fallback)
        self.assertEqual(append.await_args.kwargs["output_reason"], "no_evidence")
