import unittest
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, Mock

from pydantic import BaseModel

from app.ai.prompts.prompt_message import PromptMessage
from app.schemas.chat import (
    PlaceResult,
    PlaceResultsBlock,
    SearchBlock,
    SearchSource,
    SearchSourceSummary,
    SourceReference,
    TextBlock,
    ThinkingBlock,
    UrlBlock,
)
from app.schemas.content_block_registry import CONTENT_BLOCK_REGISTRY, ContentBlockRegistration
from app.services.source_evidence_ledger import stable_web_evidence_id
from app.services.stream import tool_round as tool_round_module
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import (
    ToolRoundOutcome,
    build_assistant_tool_message,
    handle_tool_calls_round,
    restore_reasoning_after_tool_decision,
)
from app.services.stream_state_service import StreamWriteTerminalError
from app.services.tool_handlers.base import ToolResult


class FutureRegisteredResultBlock(BaseModel):
    type: Literal["future_results"]
    id: str
    schema_version: Literal[1]


class ToolRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_train_call_preserves_model_category_before_execution(self):
        tool_call = {
            "id": "tc-train",
            "name": "search_trains",
            "arguments": '{"origin":"北京","destination":"上海","train_category":"all"}',
        }
        handler = Mock()
        handler.format_llm_context.return_value = "查询完成"
        handler.build_content_block.return_value = None
        execute_tools_fn = AsyncMock(
            return_value=[
                ToolExecutionRecord(
                    tool_call=tool_call,
                    result=ToolResult(status="success"),
                    handler=handler,
                    block_id="blk-train",
                    log_id="log-train",
                )
            ]
        )
        request = tool_round_module.ToolRoundRequest(
            db="db",
            assistant_message_id="msg-train",
            conversation_id="conv-train",
            user_id="user-1",
            model_id="gpt-4",
            provider="openai",
            content_blocks=[],
            messages=[{"role": "user", "content": "查北京到上海的高铁"}],
            tool_calls=[tool_call],
            reasoning_buf="",
            should_use_reasoning=False,
            step_context=AgentStepContext(
                step_id="step-1",
                step_number=1,
                started_at=1.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            ),
            step_number=1,
            run_id="run-1",
            emitter=AsyncMock(),
            session_cache=object(),
            network_budget=object(),
            call_kwargs={},
            persist_message_fn=Mock(),
            execute_tools_fn=execute_tools_fn,
            complete_step_fn=AsyncMock(),
        )

        await handle_tool_calls_round(request=request)

        executed_calls = execute_tools_fn.await_args.args[0]
        self.assertEqual(executed_calls[0]["arguments"], tool_call["arguments"])

    async def test_deferred_plan_control_round_persists_visible_reasoning_but_returns_raw_to_model(self):
        update_call = {
            "id": "tc-plan",
            "name": "update_plan",
            "arguments": {
                "reason": "先搜索再回答",
                "items": [
                    {
                        "id": "search",
                        "title": "搜索资料",
                        "status": "running",
                        "kind": "search",
                        "depends_on": [],
                        "planned_tools": ["web_search"],
                    },
                    {
                        "id": "answer",
                        "title": "整理回答",
                        "status": "pending",
                        "kind": "answer",
                        "depends_on": ["search"],
                        "planned_tools": [],
                    },
                ],
            },
        }
        persisted = Mock()
        state = AgentLoopState()
        state.plan_coordinator.run_id = "run-deferred"
        state.plan_coordinator.mode = "on"
        request = tool_round_module.ToolRoundRequest(
            db="db",
            assistant_message_id="msg-deferred",
            conversation_id="conv-deferred",
            user_id="user-1",
            model_id="kimi-k3",
            provider="moonshot",
            content_blocks=[],
            messages=[{"role": "user", "content": "制定计划"}],
            tool_calls=[update_call],
            reasoning_buf="内部对应计划步骤推理",
            protocol_reasoning_buf="内部 _plan_item_id 推理",
            should_use_reasoning=True,
            step_context=AgentStepContext(
                step_id="step-deferred",
                run_id="run-deferred",
                step_number=1,
                started_at=1.0,
                thinking_block_id="thinking-deferred",
                text_block_id="text-deferred",
            ),
            step_number=1,
            run_id="run-deferred",
            emitter=AsyncMock(),
            session_cache=object(),
            network_budget=object(),
            call_kwargs={},
            persist_message_fn=persisted,
            execute_tools_fn=AsyncMock(return_value=[]),
            complete_step_fn=AsyncMock(),
            announced_tool_names=frozenset({"update_plan"}),
            agent_state=state,
            output_deferred=True,
            allow_deferred_reasoning_output=True,
        )

        await handle_tool_calls_round(request=request)

        thinking_blocks = [block for block in request.content_blocks if isinstance(block, ThinkingBlock)]
        self.assertEqual(len(thinking_blocks), 1)
        self.assertEqual(thinking_blocks[0].thinking, "内部对应计划步骤推理")
        self.assertTrue(
            any(any(isinstance(block, ThinkingBlock) for block in call.args[4]) for call in persisted.call_args_list)
        )
        assistant_message = next(message for message in request.messages if message.get("role") == "assistant")
        self.assertEqual(assistant_message["reasoning_content"], "内部 _plan_item_id 推理")
        transcript_calls = [entry for entry in state.tool_transcript if entry["role"] == "assistant"]
        self.assertEqual(len(transcript_calls), 1)
        self.assertNotIn("reasoning_content", transcript_calls[0])
        self.assertEqual(
            [entry["tool_call_id"] for entry in state.tool_transcript if entry["role"] == "tool"],
            [call["id"] for call in transcript_calls[0]["tool_calls"]],
        )

    def test_non_k3_deferred_round_adds_visible_reasoning_block(self):
        request = SimpleNamespace(
            reasoning_buf="只供内部继续调用的推理",
            output_deferred=True,
            allow_deferred_reasoning_output=True,
            model_id="gpt-4",
            content_blocks=[],
            step_context=SimpleNamespace(thinking_block_id="thinking-non-k3"),
        )

        tool_round_module.append_tool_round_reasoning(request)

        self.assertEqual(len(request.content_blocks), 1)
        self.assertEqual(request.content_blocks[0].thinking, "只供内部继续调用的推理")

    def test_plan_item_status_aggregates_all_results_and_includes_reused_success(self):
        def record(
            *,
            tool_call_id: str,
            plan_item_id: str,
            status: str,
            tool_name: str = "web_search",
            retryable: bool = False,
            reused: bool = False,
        ) -> ToolExecutionRecord:
            data = {"repair": {"retryable": True}} if retryable else {}
            return ToolExecutionRecord(
                tool_call={
                    "id": tool_call_id,
                    "name": tool_name,
                    "plan_item_id": plan_item_id,
                },
                result=ToolResult(status=status, data=data),
                handler=None,
                block_id=f"blk-{tool_call_id}",
                log_id=f"log-{tool_call_id}",
                reused=reused,
            )

        statuses = tool_round_module._plan_item_statuses_from_results(
            [
                record(tool_call_id="ok", plan_item_id="research", status="success"),
                record(
                    tool_call_id="retry",
                    plan_item_id="research",
                    status="failed",
                    retryable=True,
                ),
                record(tool_call_id="failed", plan_item_id="research", status="failed"),
                record(
                    tool_call_id="reused",
                    plan_item_id="cached",
                    status="success",
                    reused=True,
                ),
            ]
        )

        self.assertEqual(statuses, {"research": "failed", "cached": "completed"})

    def test_retryable_failure_stays_running_then_same_plan_item_can_complete(self):
        def record(*, tool_call_id: str, status: str, retryable: bool = False) -> ToolExecutionRecord:
            data = {"repair": {"retryable": True}} if retryable else {}
            return ToolExecutionRecord(
                tool_call={
                    "id": tool_call_id,
                    "name": "web_search",
                    "plan_item_id": "search",
                },
                result=ToolResult(status=status, data=data),
                handler=None,
                block_id=f"blk-{tool_call_id}",
                log_id=f"log-{tool_call_id}",
            )

        retryable = tool_round_module._plan_item_statuses_from_results(
            [record(tool_call_id="tc-retryable", status="failed", retryable=True)]
        )
        repaired = tool_round_module._plan_item_statuses_from_results(
            [record(tool_call_id="tc-success", status="success")]
        )

        self.assertEqual(retryable, {"search": "running"})
        self.assertEqual(repaired, {"search": "completed"})

    def test_missing_result_and_non_retryable_failure_never_complete_plan_item(self):
        missing = tool_round_module._plan_item_statuses_from_batch(
            [],
            missing_result_tool_calls=[
                {
                    "id": "tc-missing",
                    "name": "web_search",
                    "plan_item_id": "missing",
                }
            ],
        )
        failed = tool_round_module._plan_item_statuses_from_results(
            [
                ToolExecutionRecord(
                    tool_call={
                        "id": "tc-failed",
                        "name": "web_search",
                        "plan_item_id": "failed",
                    },
                    result=ToolResult(
                        status="failed",
                        data={"repair": {"retryable": False}},
                    ),
                    handler=None,
                    block_id="blk-failed",
                    log_id="log-failed",
                )
            ]
        )

        self.assertNotEqual(missing["missing"], "completed")
        self.assertNotEqual(failed["failed"], "completed")

    def test_degraded_network_result_does_not_complete_plan_item(self):
        def record(tool_name: str, plan_item_id: str) -> ToolExecutionRecord:
            return ToolExecutionRecord(
                tool_call={
                    "id": f"tc-{plan_item_id}",
                    "name": tool_name,
                    "plan_item_id": plan_item_id,
                },
                result=ToolResult(status="degraded", data={}),
                handler=None,
                block_id=f"blk-{plan_item_id}",
                log_id=f"log-{plan_item_id}",
            )

        statuses = tool_round_module._plan_item_statuses_from_results(
            [
                record("web_search", "search"),
                record("url_read", "read"),
                record("route_compare", "route"),
            ]
        )

        self.assertEqual(
            statuses,
            {
                "search": "failed",
                "read": "failed",
                "route": "completed",
            },
        )

    async def test_invalid_control_call_is_paired_without_protocol_payload_leak(self):
        state = AgentLoopState()
        state.plan_coordinator.run_id = "run-1"
        request = tool_round_module.ToolRoundRequest(
            db="db",
            assistant_message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            model_id="gpt-4",
            provider="openai",
            content_blocks=[],
            messages=[{"role": "user", "content": "复杂任务"}],
            tool_calls=[{"id": "tc-plan", "name": "update_plan", "arguments": "<｜DSML｜>broken"}],
            reasoning_buf="",
            should_use_reasoning=False,
            step_context=AgentStepContext(
                step_id="step-1",
                run_id="run-1",
                step_number=1,
                started_at=1.0,
                thinking_block_id="thinking",
                text_block_id="text",
            ),
            step_number=1,
            run_id="run-1",
            emitter=AsyncMock(),
            session_cache=object(),
            network_budget=object(),
            call_kwargs={},
            persist_message_fn=Mock(),
            execute_tools_fn=AsyncMock(),
            complete_step_fn=AsyncMock(),
            on_tools_executed=state.record_executed_tool_calls,
            completed_tool_calls=0,
            max_tool_calls=20,
            announced_tool_names=frozenset({"update_plan"}),
            agent_state=state,
        )

        outcome = await handle_tool_calls_round(request=request)

        self.assertEqual(outcome.tool_call_count, 0)
        response = next(message for message in request.messages if message.get("role") == "tool")
        self.assertIn("rejected", response["content"])
        self.assertNotIn("DSML", response["content"])
        self.assertEqual(request.content_blocks, [])

    def test_pending_repairs_are_keyed_and_only_matching_success_resolves_them(self):
        state = AgentLoopState()
        first_repair = ToolExecutionRecord(
            tool_call={"id": "tc-a", "name": "weather_forecast"},
            result=ToolResult(
                status="failed",
                data={
                    "repair": {
                        "repair_id": "repair-a",
                        "required_fields": ["location"],
                        "retryable": True,
                        "requires_user_input": False,
                    }
                },
            ),
            handler=None,
            block_id="blk-a",
            log_id="log-a",
        )
        second_repair = ToolExecutionRecord(
            tool_call={"id": "tc-b", "name": "weather_forecast"},
            result=ToolResult(
                status="failed",
                data={
                    "repair": {
                        "repair_id": "repair-b",
                        "required_fields": ["location"],
                        "retryable": False,
                        "requires_user_input": True,
                    }
                },
            ),
            handler=None,
            block_id="blk-b",
            log_id="log-b",
        )

        tool_round_module._record_tool_repairs(state, [first_repair, second_repair])
        tool_round_module._record_tool_repairs(
            state,
            [
                ToolExecutionRecord(
                    tool_call={"id": "tc-a-ok", "name": "weather_forecast"},
                    result=ToolResult(status="success", data={"resolves_repair_id": "repair-a"}),
                    handler=None,
                    block_id="blk-a-ok",
                    log_id="log-a-ok",
                )
            ],
        )

        self.assertNotIn("repair-a", state.pending_tool_repairs)
        self.assertIn("repair-b", state.pending_tool_repairs)

    def test_append_tool_round_messages_uses_raw_reasoning_only_for_model_protocol(self):
        request = Mock(
            messages=[],
            content_blocks=[],
            tool_calls=[{"id": "tc-1", "name": "route_compare", "arguments": "{}"}],
            reasoning_buf="需要调用 路线比较",
            protocol_reasoning_buf="需要调用 route_compare",
            should_use_reasoning=True,
        )

        tool_round_module.append_tool_round_messages_with_plan(request, [], source_plan=None)

        self.assertEqual(request.messages[0]["reasoning_content"], "需要调用 route_compare")

    def _build_product_round_request(self, *, event_error=None):
        block = PlaceResultsBlock(
            type="place_results",
            id="blk-place",
            schema_version=1,
            provider="amap",
            query="烤肉",
            status="success",
            result_count=1,
            places=[PlaceResult(provider_place_id="poi-1", name="民治烤肉店")],
            tool_call_log_id="log-place",
        )
        tool_call = {"id": "tc-place", "name": "local_place_search", "arguments": '{"query":"烤肉"}'}
        handler = Mock()
        handler.format_llm_context.return_value = "地点上下文"
        handler.build_content_block.return_value = block
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(status="success"),
            handler=handler,
            block_id="blk-place",
            log_id="log-place",
        )
        order = []
        persisted = []
        emitter = AsyncMock()

        async def emit_product_event(*, tool_call_id, content_block):
            order.append(("emit", tool_call_id, content_block))
            if event_error is not None:
                raise event_error

        emitter.content_block_upserted.side_effect = emit_product_event

        def persist_message_fn(db, msg_id, conv_id, model_id, blocks, usage_data=None, partial=False):
            snapshot = list(blocks)
            persisted.append(snapshot)
            order.append(("persist", partial, snapshot))

        async def execute_tools_fn(*args, **kwargs):
            order.append(("execute",))
            return [record]

        async def complete_step_fn(**kwargs):
            order.append(("complete",))

        request = tool_round_module.ToolRoundRequest(
            db="db",
            assistant_message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            model_id="gpt-4",
            provider="openai",
            content_blocks=[],
            messages=[{"role": "user", "content": "请推荐烤肉店"}],
            tool_calls=[tool_call],
            reasoning_buf="",
            should_use_reasoning=True,
            step_context=AgentStepContext(
                step_id="step-1",
                run_id="run-1",
                step_number=1,
                started_at=10.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            ),
            step_number=1,
            run_id="run-1",
            emitter=emitter,
            session_cache=object(),
            network_budget=Mock(max_tool_calls=20, completed_tool_calls=0),
            call_kwargs={},
            persist_message_fn=persist_message_fn,
            execute_tools_fn=execute_tools_fn,
            complete_step_fn=complete_step_fn,
        )
        return request, order, persisted, block, handler

    async def test_product_result_block_is_built_once_then_appended_and_emitted_as_same_object(self):
        block = PlaceResultsBlock(
            type="place_results",
            id="blk-place",
            schema_version=1,
            provider="amap",
            query="烤肉",
            near="民治地铁站",
            status="success",
            result_count=1,
            places=[PlaceResult(provider_place_id="poi-1", name="民治烤肉店")],
            limitations=["不包含实时排队信息"],
            tool_call_log_id="log-place",
        )
        handler = Mock()
        handler.format_llm_context.return_value = "地点上下文"
        handler.build_content_block.return_value = block
        record = ToolExecutionRecord(
            tool_call={"id": "tc-place", "name": "local_place_search", "arguments": '{"query":"烤肉"}'},
            result=ToolResult(status="success"),
            handler=handler,
            block_id="blk-place",
            log_id="log-place",
        )
        emitter = AsyncMock()
        request = Mock(
            content_blocks=[],
            messages=[],
            emitter=emitter,
            tool_calls=[record.tool_call],
            reasoning_buf="",
            should_use_reasoning=False,
        )

        built = tool_round_module.build_tool_round_content_blocks([record])
        tool_round_module.append_tool_round_messages_with_plan(
            request,
            [record],
            source_plan=None,
            built_content_blocks=built,
        )
        await tool_round_module.emit_product_result_blocks(
            request,
            [record],
            built_content_blocks=built,
        )

        emitter.content_block_upserted.assert_awaited_once_with(tool_call_id="tc-place", content_block=block)
        self.assertIs(request.content_blocks[0], block)
        handler.build_content_block.assert_called_once_with(record.result, "blk-place", "log-place")

    async def test_non_terminal_product_event_failure_keeps_built_block_for_persistence(self):
        block = PlaceResultsBlock(
            type="place_results",
            id="blk-place",
            schema_version=1,
            provider="amap",
            query="烤肉",
            status="success",
            result_count=0,
            places=[],
            tool_call_log_id="log-place",
        )
        handler = Mock()
        handler.build_content_block.return_value = block
        record = ToolExecutionRecord(
            tool_call={"id": "tc-place", "name": "local_place_search", "arguments": "{}"},
            result=ToolResult(status="success"),
            handler=handler,
            block_id="blk-place",
            log_id="log-place",
        )
        emitter = AsyncMock()
        emitter.content_block_upserted.side_effect = RuntimeError("临时写入失败")
        request = Mock(emitter=emitter)
        built = tool_round_module.build_tool_round_content_blocks([record])

        with self.assertLogs("app.services.stream.tool_round", level="WARNING"):
            await tool_round_module.emit_product_result_blocks(
                request,
                [record],
                built_content_blocks=built,
            )

        self.assertIs(built["tc-place"], block)

    async def test_terminal_product_event_failure_stops_round(self):
        block = PlaceResultsBlock(
            type="place_results",
            id="blk-place",
            schema_version=1,
            provider="amap",
            query="烤肉",
            status="success",
            result_count=0,
            places=[],
        )
        handler = Mock()
        handler.build_content_block.return_value = block
        record = ToolExecutionRecord(
            tool_call={"id": "tc-place", "name": "local_place_search", "arguments": "{}"},
            result=ToolResult(status="success"),
            handler=handler,
            block_id="blk-place",
            log_id="log-place",
        )
        emitter = AsyncMock()
        emitter.content_block_upserted.side_effect = StreamWriteTerminalError("写入已终止")
        built = tool_round_module.build_tool_round_content_blocks([record])

        with self.assertRaises(StreamWriteTerminalError):
            await tool_round_module.emit_product_result_blocks(
                Mock(emitter=emitter),
                [record],
                built_content_blocks=built,
            )

    async def test_handle_product_result_checkpoints_before_event_and_completes_after_event(self):
        request, order, persisted, block, handler = self._build_product_round_request()

        outcome = await handle_tool_calls_round(request=request)

        self.assertEqual([item[0] for item in order], ["persist", "execute", "persist", "emit", "complete"])
        self.assertIs(persisted[-1][-1], block)
        self.assertIs(order[3][2], block)
        self.assertEqual(outcome.tool_names, ["local_place_search"])
        self.assertEqual(outcome.product_result_count, 1)
        handler.build_content_block.assert_called_once()

    async def test_registered_future_rich_block_is_emitted_and_counted_without_type_hardcoding(self):
        registry_key = ("future_results", 1)
        CONTENT_BLOCK_REGISTRY[registry_key] = ContentBlockRegistration(
            FutureRegisteredResultBlock,
            schema_version=1,
        )
        try:
            request, order, persisted, _, handler = self._build_product_round_request()
            future_block = FutureRegisteredResultBlock(
                type="future_results",
                id="blk-future",
                schema_version=1,
            )
            handler.build_content_block.return_value = future_block

            outcome = await handle_tool_calls_round(request=request)
        finally:
            CONTENT_BLOCK_REGISTRY.pop(registry_key, None)

        self.assertEqual(outcome.product_result_count, 1)
        self.assertIs(persisted[-1][-1], future_block)
        self.assertIs(order[3][2], future_block)
        request.emitter.content_block_upserted.assert_awaited_once_with(
            tool_call_id="tc-place",
            content_block=future_block,
        )

    async def test_terminal_product_event_failure_happens_after_partial_checkpoint(self):
        request, order, persisted, block, handler = self._build_product_round_request(
            event_error=StreamWriteTerminalError("写入已终止")
        )

        with self.assertRaises(StreamWriteTerminalError):
            await handle_tool_calls_round(request=request)

        self.assertEqual([item[0] for item in order], ["persist", "execute", "persist", "emit"])
        self.assertIs(persisted[-1][-1], block)
        self.assertIs(request.content_blocks[-1], block)
        self.assertEqual(request.messages[-1], {"role": "tool", "tool_call_id": "tc-place", "content": "地点上下文"})
        handler.build_content_block.assert_called_once()

    def test_unannounced_calls_do_not_consume_global_tool_capacity(self):
        stale_call = {"id": "tc-stale", "name": "mcp_maps_search", "arguments": "{}"}
        search_call = {"id": "tc-search", "name": "web_search", "arguments": "{}"}
        read_call = {"id": "tc-read", "name": "url_read", "arguments": "{}"}
        request = Mock(
            tool_calls=[stale_call, search_call, read_call],
            announced_tool_names=frozenset({"web_search", "url_read"}),
            completed_tool_calls=19,
            max_tool_calls=20,
            network_budget=object(),
            run_id="run-limit",
            tool_handlers={},
            unplanned_tool_names=frozenset(),
        )

        with self.assertLogs("app.services.stream.tool_round", level="WARNING") as captured:
            announced, unavailable = tool_round_module._partition_tool_calls_by_announcement(request)
        selected, global_limit_not_executed = tool_round_module._select_tool_calls_within_limit(
            request,
            announced,
        )

        self.assertEqual(announced, [search_call, read_call])
        self.assertEqual(unavailable, [stale_call])
        self.assertEqual(selected, [search_call])
        self.assertEqual(global_limit_not_executed, [read_call])
        logs = "\n".join(captured.output)
        self.assertIn("run_id=run-limit requested=3 announced=2 unavailable=1", logs)
        self.assertNotIn("mcp_maps_search", logs)
        self.assertNotIn("tc-stale", logs)

    def test_local_preflight_failure_does_not_displace_valid_call_at_global_limit(self):
        class Handler:
            def __init__(self, tool_name):
                self.tool_name = tool_name

        invalid_handler = Handler("mcp_invalid_json")
        valid_handler = Handler("mcp_valid")
        extra_handler = Handler("mcp_extra")
        invalid = {
            "id": "tc-invalid",
            "name": invalid_handler.tool_name,
            "arguments": "{bad",
        }
        valid = {
            "id": "tc-valid",
            "name": valid_handler.tool_name,
            "arguments": "{}",
        }
        extra = {
            "id": "tc-extra",
            "name": extra_handler.tool_name,
            "arguments": "{}",
        }
        request = Mock(
            tool_calls=[invalid, valid, extra],
            completed_tool_calls=19,
            max_tool_calls=20,
            network_budget=object(),
            run_id="run-preflight-limit",
            agent_state=None,
            tool_handlers={
                invalid_handler.tool_name: invalid_handler,
                valid_handler.tool_name: valid_handler,
                extra_handler.tool_name: extra_handler,
            },
            unplanned_tool_names=frozenset(),
        )

        selected, not_executed = tool_round_module._select_tool_calls_within_limit(request)

        self.assertEqual(selected, [invalid, valid])
        self.assertEqual(not_executed, [extra])

    def test_document_output_tools_are_not_cut_by_lookup_limit(self):
        document_call = {"id": "tc-doc", "name": "create_document", "arguments": "{}"}
        lookup_call = {"id": "tc-search", "name": "web_search", "arguments": "{}"}
        request = Mock(
            tool_calls=[document_call, lookup_call],
            completed_tool_calls=20,
            max_tool_calls=20,
            network_budget=object(),
            run_id="run-document-limit",
            agent_state=None,
            tool_handlers={},
            unplanned_tool_names=frozenset({"create_document"}),
        )

        selected, not_executed = tool_round_module._select_tool_calls_within_limit(request)

        self.assertEqual(selected, [document_call])
        self.assertEqual(not_executed, [lookup_call])

    def test_local_preflight_failures_are_bounded_after_global_limit_is_exhausted(self):
        class Handler:
            tool_name = "mcp_invalid_json"

        handler = Handler()
        invalid_calls = [
            {
                "id": f"tc-invalid-{index}",
                "name": handler.tool_name,
                "arguments": "{bad",
            }
            for index in range(100)
        ]
        request = Mock(
            tool_calls=invalid_calls,
            completed_tool_calls=20,
            max_tool_calls=20,
            network_budget=object(),
            run_id="run-preflight-attempt-cap",
            agent_state=None,
            tool_handlers={handler.tool_name: handler},
            unplanned_tool_names=frozenset(),
        )

        selected, not_executed = tool_round_module._select_tool_calls_within_limit(request)

        self.assertEqual(
            len(selected),
            tool_round_module.MAX_LOCAL_PREFLIGHT_ATTEMPTS_PER_ROUND,
        )
        self.assertEqual(
            len(not_executed),
            len(invalid_calls) - tool_round_module.MAX_LOCAL_PREFLIGHT_ATTEMPTS_PER_ROUND,
        )

    def test_local_preflight_result_does_not_increment_actual_global_tool_count(self):
        tool_call = {
            "id": "tc-context7-unresolved",
            "name": "mcp_context7_query",
            "arguments": "{}",
        }
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(
                status="failed",
                data={
                    "error_code": "context7_library_id_unresolved",
                    "local_preflight": True,
                },
            ),
            handler=None,
            block_id="block-context7",
            log_id="log-context7",
        )

        self.assertEqual(
            tool_round_module._actual_tool_execution_count([tool_call], [record]),
            0,
        )

    def test_unknown_tool_with_invalid_json_cannot_bypass_global_limit(self):
        unknown_calls = [
            {
                "id": f"tc-unknown-{index}",
                "name": "mcp_runtime_handler_missing",
                "arguments": "{bad",
            }
            for index in range(100)
        ]
        request = Mock(
            tool_calls=unknown_calls,
            completed_tool_calls=20,
            max_tool_calls=20,
            network_budget=object(),
            run_id="run-unknown-handler-limit",
            agent_state=None,
            tool_handlers={},
            unplanned_tool_names=frozenset(),
        )

        selected, not_executed = tool_round_module._select_tool_calls_within_limit(request)

        self.assertEqual(selected, [])
        self.assertEqual(not_executed, unknown_calls)

    def test_build_assistant_tool_message_preserves_tool_calls_and_reasoning(self):
        tool_calls = [
            {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'},
            {"id": "tc-2", "name": "url_read", "arguments": '{"url":"https://example.com"}'},
        ]

        message = build_assistant_tool_message(
            tool_calls=tool_calls,
            reasoning_buf="需要搜索",
            should_use_reasoning=True,
        )

        self.assertEqual(
            message,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                    },
                    {
                        "id": "tc-2",
                        "type": "function",
                        "function": {"name": "url_read", "arguments": '{"url":"https://example.com"}'},
                    },
                ],
                "reasoning_content": "需要搜索",
            },
        )
        self.assertIsInstance(message, PromptMessage)
        self.assertIsNone(message.section_id)

        no_reasoning = build_assistant_tool_message(
            tool_calls=tool_calls,
            reasoning_buf="需要搜索",
            should_use_reasoning=False,
        )
        self.assertNotIn("reasoning_content", no_reasoning)

        empty_reasoning = build_assistant_tool_message(
            tool_calls=tool_calls,
            reasoning_buf="",
            should_use_reasoning=True,
        )
        self.assertNotIn("reasoning_content", empty_reasoning)

        raw_content = build_assistant_tool_message(
            tool_calls=tool_calls,
            reasoning_buf="标签内思考",
            should_use_reasoning=True,
            protocol_content_buf="<think>标签内思考</think>",
        )
        self.assertEqual(raw_content["content"], "<think>标签内思考</think>")
        self.assertNotIn("reasoning_content", raw_content)

    def test_restore_reasoning_after_tool_decision_keeps_tool_round_compatibility(self):
        call_kwargs = {
            "tools": [{"function": {"name": "web_search"}}],
            "extra_body": {"thinking": {"type": "disabled"}, "trace": "kept"},
        }

        restore_reasoning_after_tool_decision(call_kwargs, provider="openai")

        self.assertEqual(
            call_kwargs["extra_body"],
            {"thinking": {"type": "disabled"}, "trace": "kept"},
        )

    def test_restore_reasoning_after_tool_decision_only_removes_disabled_thinking_without_tools(self):
        call_kwargs = {
            "extra_body": {"thinking": {"type": "disabled"}, "trace": "kept"},
        }

        restore_reasoning_after_tool_decision(call_kwargs, provider="volcengine")

        self.assertEqual(call_kwargs["extra_body"], {"trace": "kept"})

    def test_deepseek_enabled_thinking_is_never_removed(self):
        call_kwargs = {
            "tools": [{"function": {"name": "update_plan"}}],
            "extra_body": {"thinking": {"type": "enabled"}},
        }

        restore_reasoning_after_tool_decision(call_kwargs, provider="deepseek")

        self.assertEqual(call_kwargs["extra_body"], {"thinking": {"type": "enabled"}})

    async def test_handle_tool_calls_round_accepts_request_and_preserves_sequence(self):
        request_cls = getattr(tool_round_module, "ToolRoundRequest")
        tool_call = {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'}
        content_block = TextBlock(type="text", id="blk_tool", text="工具摘要")
        handler = Mock()
        handler.format_llm_context.return_value = "LLM 可见工具上下文"
        handler.build_content_block.return_value = content_block
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(status="success", duration_ms=12),
            handler=handler,
            block_id="blk_tool",
            log_id="log-1",
        )
        messages = [{"role": "user", "content": "hi"}]
        content_blocks = []
        call_kwargs = {"extra_body": {"thinking": {"type": "disabled"}}}
        emitter = object()
        session_cache = object()
        network_budget = object()
        step_context = AgentStepContext(
            step_id="step-1",
            step_number=3,
            started_at=10.0,
            thinking_block_id="blk_thinking",
            text_block_id="blk_text",
        )
        order = []

        def persist_message_fn(db, msg_id, conv_id, model_id, blocks, usage_data=None, partial=False):
            order.append(("persist", partial, [getattr(block, "id", None) for block in blocks], usage_data))

        async def execute_tools_fn(tool_calls, conversation_id, user_id, model_id, provider, **kwargs):
            order.append(("execute", tool_calls, conversation_id, user_id, model_id, provider, kwargs))
            return [record]

        async def complete_step_fn(
            *,
            context,
            emitter,
            session_cache,
            tool_names,
            tool_call_count,
            completed_tool_calls,
            max_tool_calls,
            clock,
        ):
            order.append(
                (
                    "complete",
                    tool_names,
                    tool_call_count,
                    completed_tool_calls,
                    max_tool_calls,
                    clock(),
                    "extra_body" in call_kwargs,
                )
            )

        def on_tools_executed(tool_call_count):
            order.append(("record", tool_call_count))

        request = request_cls(
            db="db",
            assistant_message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            model_id="gpt-4",
            provider="openai",
            content_blocks=content_blocks,
            messages=messages,
            tool_calls=[tool_call],
            reasoning_buf="需要搜索",
            should_use_reasoning=True,
            step_context=step_context,
            step_number=3,
            run_id="run-1",
            emitter=emitter,
            session_cache=session_cache,
            network_budget=network_budget,
            call_kwargs=call_kwargs,
            persist_message_fn=persist_message_fn,
            execute_tools_fn=execute_tools_fn,
            complete_step_fn=complete_step_fn,
            on_tools_executed=on_tools_executed,
            clock=Mock(return_value=10.5),
        )

        outcome = await handle_tool_calls_round(request=request)

        self.assertEqual(
            outcome,
            ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["web_search"],
                no_progress_search_results=(False,),
            ),
        )
        self.assertEqual([entry[0] for entry in order], ["persist", "execute", "record", "persist", "complete"])
        self.assertEqual(order[0], ("persist", True, ["blk_thinking"], None))
        self.assertEqual(order[2], ("record", 1))
        self.assertEqual(order[3], ("persist", True, ["blk_thinking", "blk_tool"], None))
        self.assertEqual(order[4], ("complete", ["web_search"], 1, None, None, 10.5, True))
        self.assertNotIn("extra_body", call_kwargs)
        self.assertEqual(messages[-1], {"role": "tool", "tool_call_id": "tc-1", "content": "LLM 可见工具上下文"})

    async def test_handle_tool_calls_round_marks_plan_running_before_execute_tools(self):
        tool_call = {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'}
        handler = Mock()
        handler.format_llm_context.return_value = "LLM 可见工具上下文"
        handler.build_content_block.return_value = None
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(status="success", duration_ms=12),
            handler=handler,
            block_id="blk_tool",
            log_id="log-1",
        )
        order = []
        original_mark_tool_round_started = getattr(tool_round_module, "mark_tool_round_started", None)

        async def mark_tool_round_started(**kwargs):
            order.append(
                (
                    "plan",
                    kwargs["context"].run_id,
                    kwargs["tool_call_count"],
                    kwargs["tool_names"],
                    kwargs["tool_arguments"],
                    kwargs["completed_tool_calls"],
                    kwargs["max_tool_calls"],
                )
            )

        async def execute_tools_fn(*args, **kwargs):
            order.append(("execute", kwargs["trace_id"], kwargs["network_budget"].max_tool_calls))
            return [record]

        async def complete_step_fn(**kwargs):
            order.append(("complete", kwargs["tool_names"], kwargs["tool_call_count"]))

        tool_round_module.mark_tool_round_started = mark_tool_round_started
        try:
            await handle_tool_calls_round(
                request=tool_round_module.ToolRoundRequest(
                    db="db",
                    assistant_message_id="msg-1",
                    conversation_id="conv-1",
                    user_id="user-1",
                    model_id="gpt-4",
                    provider="openai",
                    content_blocks=[],
                    messages=[{"role": "user", "content": "hi"}],
                    tool_calls=[tool_call],
                    reasoning_buf="",
                    should_use_reasoning=True,
                    step_context=AgentStepContext(
                        step_id="step-1",
                        run_id="run-1",
                        step_number=1,
                        started_at=10.0,
                        thinking_block_id="blk_thinking",
                        text_block_id="blk_text",
                    ),
                    step_number=1,
                    run_id="run-1",
                    emitter=object(),
                    session_cache=object(),
                    network_budget=Mock(max_tool_calls=20, completed_tool_calls=0),
                    call_kwargs={},
                    persist_message_fn=Mock(),
                    execute_tools_fn=execute_tools_fn,
                    complete_step_fn=complete_step_fn,
                )
            )
        finally:
            if original_mark_tool_round_started is None:
                delattr(tool_round_module, "mark_tool_round_started")
            else:
                tool_round_module.mark_tool_round_started = original_mark_tool_round_started

        self.assertEqual(
            order,
            [
                ("plan", "run-1", 1, ["web_search"], [{"query": "x"}], 0, 20),
                ("execute", "run-1", 20),
                ("complete", ["web_search"], 1),
            ],
        )

    async def test_handle_tool_calls_round_preserves_order_and_mutates_state(self):
        tool_call = {"id": "tc-1", "name": "web_search", "arguments": '{"query":"x"}'}
        content_block = TextBlock(type="text", id="blk_tool", text="工具摘要")
        handler = Mock()
        handler.format_llm_context.return_value = "LLM 可见工具上下文"
        handler.build_content_block.return_value = content_block
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(status="success", duration_ms=12),
            handler=handler,
            block_id="blk_tool",
            log_id="log-1",
        )
        messages = [{"role": "user", "content": "hi"}]
        content_blocks = []
        call_kwargs = {"extra_body": {"thinking": {"type": "disabled"}}}
        emitter = object()
        session_cache = object()
        network_budget = object()
        step_context = AgentStepContext(
            step_id="step-1",
            step_number=3,
            started_at=10.0,
            thinking_block_id="blk_thinking",
            text_block_id="blk_text",
        )
        order = []

        def persist_message_fn(db, msg_id, conv_id, model_id, blocks, usage_data=None, partial=False):
            order.append(
                (
                    "persist",
                    msg_id,
                    conv_id,
                    model_id,
                    partial,
                    [getattr(block, "id", None) for block in blocks],
                    usage_data,
                )
            )

        async def execute_tools_fn(tool_calls, conversation_id, user_id, model_id, provider, **kwargs):
            order.append(
                (
                    "execute",
                    tool_calls,
                    conversation_id,
                    user_id,
                    model_id,
                    provider,
                    kwargs,
                )
            )
            return [record]

        async def complete_step_fn(
            *,
            context,
            emitter,
            session_cache,
            tool_names,
            tool_call_count,
            completed_tool_calls,
            max_tool_calls,
            clock,
        ):
            order.append(
                (
                    "complete",
                    context,
                    emitter,
                    session_cache,
                    tool_names,
                    tool_call_count,
                    completed_tool_calls,
                    max_tool_calls,
                    clock(),
                    "extra_body" in call_kwargs,
                )
            )

        def on_tools_executed(tool_call_count):
            order.append(("record", tool_call_count))

        outcome = await handle_tool_calls_round(
            request=tool_round_module.ToolRoundRequest(
                db="db",
                assistant_message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                model_id="gpt-4",
                provider="openai",
                content_blocks=content_blocks,
                messages=messages,
                tool_calls=[tool_call],
                reasoning_buf="需要搜索",
                should_use_reasoning=True,
                step_context=step_context,
                step_number=3,
                run_id="run-1",
                emitter=emitter,
                session_cache=session_cache,
                network_budget=network_budget,
                call_kwargs=call_kwargs,
                persist_message_fn=persist_message_fn,
                execute_tools_fn=execute_tools_fn,
                complete_step_fn=complete_step_fn,
                on_tools_executed=on_tools_executed,
                clock=Mock(return_value=10.5),
            ),
        )

        self.assertEqual(
            outcome,
            ToolRoundOutcome(
                tool_call_count=1,
                tool_names=["web_search"],
                no_progress_search_results=(False,),
            ),
        )
        self.assertEqual([entry[0] for entry in order], ["persist", "execute", "record", "persist", "complete"])
        self.assertEqual(order[0], ("persist", "msg-1", "conv-1", "gpt-4", True, ["blk_thinking"], None))
        self.assertEqual(order[2], ("record", 1))
        self.assertEqual(order[3], ("persist", "msg-1", "conv-1", "gpt-4", True, ["blk_thinking", "blk_tool"], None))
        execute_kwargs = order[1][6]
        self.assertEqual(order[1][:6], ("execute", [tool_call], "conv-1", "user-1", "gpt-4", "openai"))
        self.assertEqual(execute_kwargs["message_id"], "msg-1")
        self.assertEqual(execute_kwargs["trace_id"], "run-1")
        self.assertEqual(execute_kwargs["step_number"], 3)
        self.assertIs(execute_kwargs["emitter"], emitter)
        self.assertIs(execute_kwargs["network_budget"], network_budget)
        self.assertEqual(
            order[4],
            ("complete", step_context, emitter, session_cache, ["web_search"], 1, None, None, 10.5, True),
        )
        self.assertNotIn("extra_body", call_kwargs)

        self.assertEqual(len(content_blocks), 2)
        self.assertEqual(content_blocks[0].type, "thinking")
        self.assertEqual(content_blocks[0].id, "blk_thinking")
        self.assertEqual(content_blocks[0].thinking, "需要搜索")
        self.assertIs(content_blocks[1], content_block)
        self.assertEqual(
            messages[1],
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                    }
                ],
                "reasoning_content": "需要搜索",
            },
        )
        self.assertEqual(
            messages[2],
            {"role": "tool", "tool_call_id": "tc-1", "content": "LLM 可见工具上下文"},
        )
        handler.format_llm_context.assert_called_once_with(record.result)
        handler.build_content_block.assert_called_once_with(record.result, "blk_tool", "log-1")

    def test_append_tool_round_messages_adds_round_level_source_selection_guidance(self):
        tool_call_1 = {"id": "tc-search-1", "name": "web_search", "arguments": '{"query":"news"}'}
        tool_call_2 = {"id": "tc-search-2", "name": "web_search", "arguments": '{"query":"official"}'}
        handler_1 = Mock()
        handler_1.format_llm_context.return_value = "第一个搜索上下文"
        handler_1.build_content_block.return_value = None
        handler_2 = Mock()
        handler_2.format_llm_context.return_value = "第二个搜索上下文"
        handler_2.build_content_block.return_value = None

        record_1 = ToolExecutionRecord(
            tool_call=tool_call_1,
            result=ToolResult(
                status="success",
                data={
                    "query": "OpenAI GPT-5.6 Sol 发布 2026年6月 新闻",
                    "intent": "freshness",
                    "search_budget": "freshness",
                    "sources": [
                        SearchSource(
                            title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                            url="https://openai.com/index/previewing-gpt-5-6-sol",
                            description="OpenAI official announcement.",
                        ),
                        SearchSource(
                            title="不過，這次GPT-5.6 並未全面開放。應美國政府要求，OpenAI 先以 ...",
                            url="https://threads.com/@kufutw/post/DaJ2_DLD8cS",
                            description="Social repost.",
                        ),
                    ],
                },
            ),
            handler=handler_1,
            block_id="blk-search-1",
            log_id="log-search-1",
        )
        record_2 = ToolExecutionRecord(
            tool_call=tool_call_2,
            result=ToolResult(
                status="success",
                data={
                    "query": "OpenAI GPT-5.6 Sol 2026年6月 官方公告",
                    "intent": "official_source",
                    "search_budget": "official_source",
                    "sources": [
                        SearchSource(
                            title="OpenAI releases powerful new GPT-5.6 model - Axios",
                            url="https://axios.com/2026/06/26/openai-gpt-sol-terra-luna-trump",
                            description="Axios reports on OpenAI GPT-5.6 release restrictions.",
                        ),
                        SearchSource(
                            title="[PDF] GPT-5.6 Preview System Card - Deployment Safety Hub",
                            url="https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf",
                            description="Official system card PDF.",
                        ),
                    ],
                },
            ),
            handler=handler_2,
            block_id="blk-search-2",
            log_id="log-search-2",
        )
        messages = [{"role": "user", "content": "请搜索"}]

        tool_round_module.append_tool_round_messages(
            tool_round_module.ToolRoundRequest(
                db="db",
                assistant_message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                model_id="gpt-4",
                provider="openai",
                content_blocks=[],
                messages=messages,
                tool_calls=[tool_call_1, tool_call_2],
                reasoning_buf="",
                should_use_reasoning=True,
                step_context=AgentStepContext(
                    step_id="step-1",
                    run_id="run-1",
                    step_number=1,
                    started_at=10.0,
                    thinking_block_id="blk_thinking",
                    text_block_id="blk_text",
                ),
                step_number=1,
                run_id="run-1",
                emitter=object(),
                session_cache=object(),
                network_budget=object(),
                call_kwargs={},
                persist_message_fn=Mock(),
                execute_tools_fn=Mock(),
                complete_step_fn=Mock(),
            ),
            [record_1, record_2],
        )

        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[2]["tool_call_id"], "tc-search-1")
        self.assertEqual(messages[2]["content"], "第一个搜索上下文")
        self.assertEqual(messages[3]["tool_call_id"], "tc-search-2")
        self.assertIn("第二个搜索上下文", messages[3]["content"])
        self.assertIn("[Search candidates]", messages[3]["content"])
        self.assertNotIn("Read at most", messages[3]["content"])
        self.assertIn("Previewing GPT-5.6 Sol", messages[3]["content"])
        self.assertIn("GPT-5.6 Preview System Card", messages[3]["content"])
        self.assertIn("Axios", messages[3]["content"])
        self.assertNotIn("Low-priority", messages[3]["content"])
        self.assertIn("threads.com", messages[3]["content"])
        self.assertNotIn("Reasons not recommended", messages[3]["content"])
        self.assertNotIn("Read at least", messages[3]["content"])
        self.assertIn("not a reading quota or a quality ranking", messages[3]["content"])

    def test_append_tool_round_messages_keeps_citations_unique_across_search_rounds(self):
        from app.services.tool_handlers.web_search import WebSearchHandler

        previous_refs = [
            SourceReference(kind="search", title=f"第一轮来源 {index}", url=f"https://first.example.com/{index}")
            for index in range(1, 6)
        ]
        previous_block = SearchBlock(
            type="search",
            id="blk-previous",
            query="第一轮搜索",
            sources=[SearchSourceSummary(title=ref.title, url=ref.url) for ref in previous_refs],
            source_refs=previous_refs,
            source_count=len(previous_refs),
        )
        tool_call = {"id": "tc-search-next", "name": "web_search", "arguments": '{"query":"第二轮搜索"}'}
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(
                status="success",
                data={
                    "query": "第二轮搜索",
                    "sources": [
                        SearchSource(
                            title="第一轮重复来源",
                            url="https://first.example.com/2",
                            description="重复来源应复用原编号",
                        ),
                        SearchSource(
                            title="第二轮新增来源",
                            url="https://second.example.com/new",
                            description="新增来源应继续编号",
                        ),
                    ],
                },
            ),
            handler=WebSearchHandler(),
            block_id="blk-next",
            log_id="log-next",
        )
        messages = [{"role": "user", "content": "请继续搜索"}]
        content_blocks = [previous_block]

        tool_round_module.append_tool_round_messages(
            tool_round_module.ToolRoundRequest(
                db="db",
                assistant_message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                model_id="gpt-4",
                provider="openai",
                content_blocks=content_blocks,
                messages=messages,
                tool_calls=[tool_call],
                reasoning_buf="",
                should_use_reasoning=True,
                step_context=AgentStepContext(
                    step_id="step-2",
                    run_id="run-1",
                    step_number=2,
                    started_at=10.0,
                    thinking_block_id="blk-thinking",
                    text_block_id="blk-text",
                ),
                step_number=2,
                run_id="run-1",
                emitter=object(),
                session_cache=object(),
                network_budget=object(),
                call_kwargs={},
                persist_message_fn=Mock(),
                execute_tools_fn=Mock(),
                complete_step_fn=Mock(),
            ),
            [record],
        )

        context = messages[-1]["content"]
        self.assertIn("[2] 第一轮重复来源", context)
        self.assertIn("[6] 第二轮新增来源", context)
        self.assertNotIn("[1] 第一轮重复来源", context)
        next_block = content_blocks[-1]
        self.assertIsInstance(next_block, SearchBlock)
        self.assertEqual(
            [ref.citation_index for ref in next_block.source_refs],
            [2, 6],
        )
        self.assertEqual(
            next_block.source_refs[0].evidence_id,
            stable_web_evidence_id("https://first.example.com/2", fallback="unused"),
        )

    def test_url_read_reuses_search_citation_metadata_for_same_canonical_url(self):
        from app.services.tool_handlers.url_read import UrlReadHandler

        url = "https://example.com/report?utm_source=search"
        evidence_id = stable_web_evidence_id(url, fallback="unused")
        previous_block = SearchBlock(
            type="search",
            query="研究报告",
            sources=[SearchSourceSummary(title="报告", url=url)],
            source_refs=[
                SourceReference(
                    kind="search",
                    title="报告",
                    url=url,
                    evidence_id=evidence_id,
                    citation_index=4,
                )
            ],
            source_count=1,
        )
        tool_call = {
            "id": "tc-read",
            "name": "url_read",
            "arguments": '{"url":"https://example.com/report"}',
        }
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(
                status="success",
                data={
                    "url": "https://example.com/report",
                    "title": "报告原文",
                    "content": "原文内容",
                },
            ),
            handler=UrlReadHandler(),
            block_id="blk-read",
            log_id="log-read",
        )
        content_blocks = [previous_block]

        tool_round_module.append_tool_round_messages(
            tool_round_module.ToolRoundRequest(
                db="db",
                assistant_message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                model_id="gpt-4",
                provider="openai",
                content_blocks=content_blocks,
                messages=[{"role": "user", "content": "读取报告"}],
                tool_calls=[tool_call],
                reasoning_buf="",
                should_use_reasoning=True,
                step_context=AgentStepContext(
                    step_id="step-read",
                    run_id="run-1",
                    step_number=2,
                    started_at=10.0,
                    thinking_block_id="blk-thinking",
                    text_block_id="blk-text",
                ),
                step_number=2,
                run_id="run-1",
                emitter=object(),
                session_cache=object(),
                network_budget=object(),
                call_kwargs={},
                persist_message_fn=Mock(),
                execute_tools_fn=Mock(),
                complete_step_fn=Mock(),
            ),
            [record],
        )

        read_block = content_blocks[-1]
        self.assertIsInstance(read_block, UrlBlock)
        self.assertEqual(read_block.source_refs[0].citation_index, 4)
        self.assertEqual(read_block.source_refs[0].evidence_id, evidence_id)

    async def test_handle_tool_calls_round_does_not_mark_unread_candidates_as_selected(self):
        tool_call = {"id": "tc-search", "name": "web_search", "arguments": '{"query":"OpenAI GPT-5.6"}'}
        handler = Mock()
        handler.format_llm_context.return_value = "搜索上下文"
        handler.build_content_block.return_value = None
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(
                status="success",
                data={
                    "query": "OpenAI GPT-5.6 官方公告",
                    "sources": [
                        SearchSource(
                            title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                            url="https://openai.com/index/previewing-gpt-5-6-sol?utm_source=feed",
                            description="OpenAI official announcement.",
                        ),
                        SearchSource(
                            title="社交平台转述",
                            url="https://threads.com/@example/post/1",
                            description="Social repost.",
                        ),
                    ],
                },
            ),
            handler=handler,
            block_id="blk-search",
            log_id="log-search",
        )
        emitter = Mock()
        emitter.evidence_item_upserted = AsyncMock()
        original_mark_tool_round_started = getattr(tool_round_module, "mark_tool_round_started", None)

        async def mark_tool_round_started(**kwargs):
            return None

        async def execute_tools_fn(*args, **kwargs):
            return [record]

        async def complete_step_fn(**kwargs):
            return None

        tool_round_module.mark_tool_round_started = mark_tool_round_started
        try:
            await handle_tool_calls_round(
                request=tool_round_module.ToolRoundRequest(
                    db="db",
                    assistant_message_id="msg-1",
                    conversation_id="conv-1",
                    user_id="user-1",
                    model_id="gpt-4",
                    provider="openai",
                    content_blocks=[],
                    messages=[{"role": "user", "content": "请搜索"}],
                    tool_calls=[tool_call],
                    reasoning_buf="",
                    should_use_reasoning=True,
                    step_context=AgentStepContext(
                        step_id="step-1",
                        run_id="run-1",
                        step_number=1,
                        started_at=10.0,
                        thinking_block_id="blk_thinking",
                        text_block_id="blk_text",
                    ),
                    step_number=1,
                    run_id="run-1",
                    emitter=emitter,
                    session_cache=object(),
                    network_budget=object(),
                    call_kwargs={},
                    persist_message_fn=Mock(),
                    execute_tools_fn=execute_tools_fn,
                    complete_step_fn=complete_step_fn,
                )
            )
        finally:
            if original_mark_tool_round_started is None:
                delattr(tool_round_module, "mark_tool_round_started")
            else:
                tool_round_module.mark_tool_round_started = original_mark_tool_round_started

        emitter.evidence_item_upserted.assert_not_awaited()

    async def test_tool_round_does_not_choose_reads_for_quick_facts(self):
        tool_call = {"id": "tc-search", "name": "web_search", "arguments": '{"query":"OpenAI GPT-5.6 是什么"}'}
        handler = Mock()
        handler.format_llm_context.return_value = "搜索上下文"
        handler.build_content_block.return_value = None
        record = ToolExecutionRecord(
            tool_call=tool_call,
            result=ToolResult(
                status="success",
                data={
                    "query": "OpenAI GPT-5.6 是什么 2026年",
                    "intent": "quick_fact",
                    "search_budget": "quick_fact",
                    "sources": [
                        SearchSource(
                            title="Previewing GPT-5.6 Sol: a next-generation model | OpenAI",
                            url="https://openai.com/index/previewing-gpt-5-6-sol",
                            description="OpenAI official announcement.",
                        ),
                        SearchSource(
                            title="[PDF] GPT-5.6 Preview System Card",
                            url="https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf",
                            description="Official system card PDF.",
                        ),
                        SearchSource(
                            title="OpenAI releases powerful new GPT-5.6 model - Axios",
                            url="https://axios.com/2026/06/26/openai-gpt-sol-terra-luna-trump",
                            description="Axios report.",
                        ),
                    ],
                },
            ),
            handler=handler,
            block_id="blk-search",
            log_id="log-search",
        )
        emitter = Mock()
        emitter.evidence_item_upserted = AsyncMock()

        await tool_round_module.emit_selected_source_evidence(
            tool_round_module.ToolRoundRequest(
                db="db",
                assistant_message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                model_id="gpt-4",
                provider="openai",
                content_blocks=[],
                messages=[{"role": "user", "content": "请搜索"}],
                tool_calls=[tool_call],
                reasoning_buf="",
                should_use_reasoning=True,
                step_context=AgentStepContext(
                    step_id="step-1",
                    run_id="run-1",
                    step_number=1,
                    started_at=10.0,
                    thinking_block_id="blk_thinking",
                    text_block_id="blk_text",
                ),
                step_number=1,
                run_id="run-1",
                emitter=emitter,
                session_cache=object(),
                network_budget=object(),
                call_kwargs={},
                persist_message_fn=Mock(),
                execute_tools_fn=Mock(),
                complete_step_fn=Mock(),
            ),
            [record],
        )

        selected_events = [
            call.kwargs["evidence"]
            for call in emitter.evidence_item_upserted.await_args_list
            if call.kwargs["evidence"]["status"] == "selected"
        ]
        self.assertEqual(selected_events, [])


class PendingPlaceChoiceTests(unittest.TestCase):
    def test_only_ambiguous_failure_with_candidates_becomes_place_choice(self):
        from app.services.stream.tool_round import _pending_place_choice

        details = {"place_name": "外滩", "candidates": [{"candidate": 1}, {"candidate": 2}]}
        ambiguous = ToolResult(status="failed", data={"error_code": "ambiguous_location", "error_details": details})
        self.assertEqual(_pending_place_choice(ambiguous), details)
        for result in (
            ToolResult(status="success", data={"error_code": "ambiguous_location", "error_details": details}),
            ToolResult(status="failed", data={"error_code": "invalid_response", "error_details": details}),
            ToolResult(status="failed", data={"error_code": "ambiguous_location"}),
            ToolResult(
                status="failed",
                data={"error_code": "ambiguous_location", "error_details": {"place_name": "外滩", "candidates": []}},
            ),
            ToolResult(
                status="failed",
                data={
                    "error_code": "ambiguous_location",
                    "error_details": details,
                    "repair": {"requires_user_input": True},
                },
            ),
        ):
            with self.subTest(result=result):
                self.assertIsNone(_pending_place_choice(result))
