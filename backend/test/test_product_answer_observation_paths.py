"""确定性早退必须记录独立分母，不能伪装成 validator 通过。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.stream import agent_loop_round_outcome as outcome
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


class ProductAnswerObservationPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_deterministic_early_return_is_observed_and_retained(self):
        paths = ("weather_activity", "mixed_travel", "single_travel_comparison")
        for selected in paths:
            with self.subTest(path=selected):
                request = outcome.AgentRoundOutcomeRequest(
                    db=None,
                    messages=[],
                    state=AgentLoopState(product_tool_attempted=True),
                    runtime=_runtime(),
                    step_number=1,
                    step_context=_step_context(),
                    round_result=AgentRoundResult(
                        reasoning_buf="",
                        content_buf="秘密模型原文",
                        tool_calls=[],
                        finish_reason="stop",
                        accumulated_usage=None,
                    ),
                )
                with (
                    patch.object(
                        outcome,
                        "build_grounded_weather_activity_answer",
                        return_value="确定性天气回答" if selected == paths[0] else "",
                    ),
                    patch.object(
                        outcome,
                        "build_grounded_mixed_travel_answer",
                        return_value="确定性混合回答" if selected == paths[1] else "",
                    ),
                    patch.object(
                        outcome,
                        "build_grounded_single_travel_comparison_answer",
                        return_value="确定性单程回答" if selected == paths[2] else "",
                    ),
                    patch.object(outcome, "_append_committed_answer", new=AsyncMock()) as append,
                    patch.object(outcome, "validate_product_answer") as validate,
                    patch.object(outcome, "emit_product_answer_observation") as emit,
                    patch.object(outcome, "retain_product_answer_observation", new=AsyncMock(), create=True) as retain,
                ):
                    await outcome._commit_deferred_product_answer(request)
                emit.assert_called_once()
                payload = emit.call_args.args[0]
                self.assertEqual(payload["observation_path"], selected)
                self.assertFalse(payload["validated"])
                self.assertIsNone(payload["is_valid"])
                self.assertEqual(payload["reason_code"], "not_validated")
                self.assertTrue(payload["product_tool_attempted"])
                retain.assert_awaited_once_with(payload)
                validate.assert_not_called()
                append.assert_awaited_once()
                self.assertNotIn("秘密模型原文", str(payload))
