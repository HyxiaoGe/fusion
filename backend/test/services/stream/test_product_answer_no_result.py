"""实测产品拦截开关与无产品结果时的用户输出，区分外层入口和内部防御路径。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.schemas.chat import PlaceResult, PlaceResultsBlock, ThinkingBlock, Usage
from app.services.stream.agent_loop_round_outcome import (
    AgentRoundOutcomeRequest,
    _commit_deferred_product_answer,
    handle_agent_round_outcome,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.product_answer_validator import repair_unsupported_product_answer, validate_product_answer
from app.services.stream.product_result_answer import build_grounded_product_answer, build_product_tool_failure_answer
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context


async def capture_product_answer_case(
    *,
    attempted: bool,
    product_result: bool = False,
    internal_entry: bool = False,
    tool_failed: bool = False,
    pending_repair: bool = False,
) -> dict:
    state = AgentLoopState(product_tool_attempted=attempted)
    state.content_blocks.append(ThinkingBlock(type="thinking", thinking="正在整理本次结果。"))
    if product_result:
        state.content_blocks.append(
            PlaceResultsBlock(
                type="place_results",
                schema_version=1,
                provider="amap",
                query="烤肉",
                status="success",
                result_count=1,
                places=[PlaceResult(name="炭火一号")],
            )
        )
    if tool_failed:
        state.record_tool_outcome("local_place_search", "failed")
    if pending_repair:
        state.pending_tool_repairs["repair-protocol"] = {"required_fields": [], "retryable": False}
    state.mark_current_step("step-no-result")
    candidate = "可以优先考虑炭火一号。这里停车方便。"
    lifecycle = AsyncMock()
    lifecycle.record_output = Mock()
    request = AgentRoundOutcomeRequest(
        db="db",
        messages=[{"role": "user", "content": "找一家烤肉店"}],
        state=state,
        runtime=_runtime(complete_step_fn=AsyncMock()),
        step_number=1,
        step_context=_step_context("step-no-result"),
        round_result=AgentRoundResult(
            reasoning_buf="",
            content_buf=candidate,
            tool_calls=[],
            finish_reason="stop",
            accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            output_deferred=True,
            llm_lifecycle=lifecycle,
        ),
    )
    grounded_answer_before_commit = build_grounded_product_answer(state.content_blocks, messages=request.messages)
    with (
        patch("app.services.stream.agent_loop_round_outcome.settings.PRODUCT_ANSWER_REPAIR_ENABLED", False),
        patch("app.services.stream.agent_loop_round_outcome.append_chunk", new_callable=AsyncMock) as append,
        patch(
            "app.services.stream.agent_loop_round_outcome.validate_product_answer", wraps=validate_product_answer
        ) as validate,
        patch(
            "app.services.stream.agent_loop_round_outcome.repair_unsupported_product_answer",
            wraps=repair_unsupported_product_answer,
        ) as repair,
        patch("app.services.stream.agent_loop_round_outcome.emit_product_answer_observation") as observation,
    ):
        outcome = (
            await _commit_deferred_product_answer(request)
            if internal_entry
            else await handle_agent_round_outcome(request=request)
        )
    return {
        "entry": "internal_product_commit" if internal_entry else "handle_agent_round_outcome",
        "product_tool_attempted": attempted,
        "product_result_present": product_result,
        "tool_failed": tool_failed,
        "pending_repair": pending_repair,
        "candidate": candidate,
        "grounded_answer_before_commit": grounded_answer_before_commit,
        "answer": append.await_args.args[2],
        "stored_text": [block.text for block in state.content_blocks if getattr(block, "type", None) == "text"],
        "validation_calls": validate.call_count,
        "repair_calls": repair.call_count,
        "observations": [call.args[0] for call in observation.call_args_list],
        "model_output_visible": lifecycle.publish_visible_output.await_count > 0,
        "unknown_terminated": state.unknown_terminated,
        "exit": None if internal_entry else outcome.exit.value,
    }


class ProductAnswerNoResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_repair_still_blocks_invalid_product_answer(self):
        result = await capture_product_answer_case(attempted=True, product_result=True)
        self.assertEqual(result["validation_calls"], 1)
        self.assertEqual(result["repair_calls"], 1)
        self.assertFalse(result["observations"][0]["repair_enabled"])
        self.assertFalse(result["observations"][0]["is_valid"])
        self.assertFalse(result["model_output_visible"])
        self.assertNotEqual(result["answer"], result["candidate"])
        self.assertNotIn("停车方便", result["answer"])
        self.assertEqual(result["stored_text"], [result["answer"]])

    async def test_thinking_only_without_product_attempt_stays_on_ordinary_path(self):
        result = await capture_product_answer_case(attempted=False)
        self.assertEqual(result["answer"], result["candidate"])
        self.assertEqual(result["validation_calls"], 0)
        self.assertEqual(result["repair_calls"], 0)
        self.assertTrue(result["model_output_visible"])
        self.assertEqual(result["stored_text"], [result["candidate"]])
        self.assertFalse(result["unknown_terminated"])

    async def test_thinking_only_with_pending_repair_clarifies_before_product_context(self):
        result = await capture_product_answer_case(attempted=False, pending_repair=True)
        self.assertEqual(result["answer"], "当前查询还缺少必要信息，请补充更明确的条件后重试；信息确认前不会猜测结果。")
        self.assertEqual(result["validation_calls"], 0)
        self.assertEqual(result["repair_calls"], 0)
        self.assertFalse(result["model_output_visible"])
        self.assertNotIn("卡片", result["answer"])
        self.assertEqual(result["stored_text"], [result["answer"]])

    async def test_thinking_only_after_product_attempt_uses_failure_answer(self):
        result = await capture_product_answer_case(attempted=True)
        self.assertEqual(result["grounded_answer_before_commit"], "")
        self.assertEqual(result["answer"], build_product_tool_failure_answer())
        self.assertEqual(result["validation_calls"], 0)
        self.assertEqual(result["repair_calls"], 0)
        self.assertEqual(result["observations"], [])
        self.assertFalse(result["model_output_visible"])
        self.assertNotIn("卡片", result["answer"])
        self.assertEqual(result["stored_text"], [result["answer"]])

    async def test_known_tool_failure_preserves_non_success_terminal_marker(self):
        result = await capture_product_answer_case(attempted=True, tool_failed=True)
        self.assertTrue(result["unknown_terminated"])
        self.assertFalse(result["model_output_visible"])
        self.assertEqual(result["validation_calls"], 0)
        self.assertNotIn("卡片", result["answer"])
        self.assertEqual(result["stored_text"], [result["answer"]])

    async def test_internal_product_commit_without_results_never_assumes_cards(self):
        # 内部函数防御性测试；外层在 attempted=False 时不会选择这个入口。
        result = await capture_product_answer_case(attempted=False, internal_entry=True)
        self.assertEqual(result["grounded_answer_before_commit"], "")
        self.assertEqual(result["answer"], build_product_tool_failure_answer())
        self.assertNotIn("卡片", result["answer"])
        self.assertEqual(result["validation_calls"], 0)
        self.assertEqual(result["repair_calls"], 0)
        self.assertEqual(result["observations"], [])
        self.assertFalse(result["model_output_visible"])


async def print_case_evidence() -> None:
    for options in (
        {"attempted": True, "product_result": True},
        {"attempted": False},
        {"attempted": True},
        {"attempted": True, "tool_failed": True},
        {"attempted": False, "pending_repair": True},
        {"attempted": False, "internal_entry": True},
    ):
        print(json.dumps(await capture_product_answer_case(**options), ensure_ascii=False))
