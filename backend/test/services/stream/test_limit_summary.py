import asyncio
import hashlib
import json
import unittest
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.prompts.agent_loop import NO_TOOL_EVIDENCE_SUMMARY_PROMPT, VISIBLE_RESPONSE_LANGUAGE_PROMPT
from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.section_ids import (
    AGENT_PLAN_CONTROL,
    APP_IDENTITY,
    DEEP_RESEARCH_CONTRACT,
    RESEARCH_EVIDENCE_WORKSET,
    TOOL_USAGE_CONTRACT,
)
from app.ai.prompts.section_ids import (
    LIMIT_SUMMARY as LIMIT_SUMMARY_SECTION_ID,
)
from app.schemas.chat import (
    ContextUsage,
    SearchBlock,
    SearchSourceSummary,
    SourceReference,
    UrlBlock,
    Usage,
)
from app.services.chat.context_manager import ContextPlan
from app.services.chat.context_manager import prepare_context as prepare_context_real
from app.services.stream import limit_summary as limit_summary_module
from app.services.stream import llm_stream as llm_stream_module
from app.services.stream.limit_summary import (
    DEEP_RESEARCH_INCOMPLETE_TEXT,
    LIMIT_SUMMARY_PROMPT,
    SUMMARY_PROTOCOL_FALLBACK_TEXT,
    LimitSummaryStepRequest,
    accumulate_summary_usage,
    append_limit_summary_prompt,
    append_summary_content_blocks,
    build_limit_summary_call_kwargs,
    compute_summary_timeout,
    remove_conflicting_tool_usage_contract,
    run_limit_summary_step,
)
from app.services.stream.research_evidence import ResearchEvidenceWorkset
from app.services.stream.run_capability_router import RunCapabilityResolution
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset
from app.services.stream_state_service import StreamOwnershipLostError
from app.services.tool_handlers.base import ToolResult


def _summary_recovery_evidence() -> RecoveryEvidenceWorkset:
    """与测试来源块对应的实际正文记录，不能仅靠卡片元数据模拟证据。"""

    evidence = RecoveryEvidenceWorkset()
    evidence.record_result(
        "web_search",
        ToolResult(
            status="success",
            data={
                "sources": [
                    {"url": "https://example.com/plan", "description": "计划查询得到的正文摘要。"},
                    {"url": "https://example.com/a", "description": "出行查询得到的正文摘要。"},
                ]
            },
        ),
    )
    evidence.record_result(
        "url_read",
        ToolResult(
            status="success",
            data={
                "url": "https://example.com/timetable",
                "content": "实际读到的班次正文。",
            },
        ),
    )
    return evidence


def _deep_summary_evidence() -> tuple[ResearchEvidenceWorkset, list]:
    blocks = [
        SearchBlock(
            type="search",
            query="研究问题",
            sources=[
                SearchSourceSummary(title="候选 A", url="https://example.com/a"),
                SearchSourceSummary(title="候选 B", url="https://example.com/b"),
            ],
            source_refs=[
                SourceReference(
                    kind="search",
                    title="候选 A",
                    url="https://example.com/a",
                    evidence_id="ev-a",
                    citation_index=2,
                ),
                SourceReference(
                    kind="search",
                    title="候选 B",
                    url="https://example.com/b",
                    evidence_id="ev-b",
                    citation_index=3,
                ),
            ],
            source_count=2,
        ),
        UrlBlock(
            type="url_read",
            url="https://example.com/a",
            title="来源 A",
            source_refs=[
                SourceReference(
                    kind="url_read",
                    title="来源 A",
                    url="https://example.com/a",
                    evidence_id="ev-a",
                    citation_index=2,
                )
            ],
            source_count=1,
        ),
        UrlBlock(
            type="url_read",
            url="https://example.com/b",
            title="来源 B",
            source_refs=[
                SourceReference(
                    kind="url_read",
                    title="来源 B",
                    url="https://example.com/b",
                    evidence_id="ev-b",
                    citation_index=3,
                )
            ],
            source_count=1,
        ),
    ]
    workset = ResearchEvidenceWorkset()
    workset.record_content_blocks(blocks)
    return workset, blocks


class LimitSummaryHelpersTests(unittest.TestCase):
    def test_limit_summary_does_not_inspect_or_rewrite_runtime_body(self):
        messages = []

        with patch.object(limit_summary_module, "get_limit_summary_prompt", return_value="任意热更新正文"):
            append_limit_summary_prompt(messages)

        self.assertEqual(messages[-1].content, "任意热更新正文")
        self.assertEqual(messages[-1].section_id, LIMIT_SUMMARY_SECTION_ID)

    def test_control_cleanup_uses_identity_instead_of_localized_body(self):
        removable = PromptMessage(
            role="system",
            content="管理员热更新后的任意计划控制正文",
            section_id="agent_plan_control",
        )
        keep = PromptMessage(
            role="system",
            content="【执行计划控制规则】只是用户要求引用的普通文本",
            section_id="user_preferences",
        )
        messages = [removable, keep, PromptMessage(role="user", content="请总结")]

        remove_conflicting_tool_usage_contract(messages)

        self.assertEqual(messages, [keep, PromptMessage(role="user", content="请总结")])

    def test_no_progress_summary_uses_neutral_prompt_without_limit_language(self):
        messages = []

        append_limit_summary_prompt(messages, summary_finish_reason="no_progress_summary")

        content = messages[-1]["content"]
        self.assertIn("searches are no longer producing useful new information", content)
        self.assertIn("Do not mention internal stop reasons", content)
        self.assertNotIn("tool-call limit", content)
        self.assertNotIn("quota", content)
        self.assertNotIn("budget", content)

    def test_build_limit_summary_call_kwargs_forbids_tool_calls_but_keeps_definitions(self):
        tools = [{"function": {"name": "web_search"}}]
        call_kwargs = {
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "extra_body": {"thinking": {"type": "disabled"}},
        }

        result = build_limit_summary_call_kwargs(call_kwargs)

        self.assertIsNot(result, call_kwargs)
        self.assertEqual(
            result,
            {
                "temperature": 0.2,
                "extra_body": {"thinking": {"type": "disabled"}},
                "tools": tools,
                "tool_choice": "none",
            },
        )
        self.assertEqual(call_kwargs["tool_choice"], "auto")
        self.assertEqual(build_limit_summary_call_kwargs({"temperature": 0.2}), {"temperature": 0.2})
        self.assertEqual(call_kwargs["tool_choice"], "auto")

    def test_compute_summary_timeout_uses_remaining_budget(self):
        timeout = compute_summary_timeout(
            total_timeout_s=300,
            run_start=100.0,
            clock=lambda: 125.5,
        )

        self.assertEqual(timeout, 274.5)

    def test_compute_summary_timeout_has_10s_floor(self):
        timeout = compute_summary_timeout(
            total_timeout_s=300,
            run_start=100.0,
            clock=lambda: 450.0,
        )

        self.assertEqual(timeout, 10)

    def test_accumulate_summary_usage_adds_usage_data(self):
        result = accumulate_summary_usage(
            Usage(input_tokens=2, output_tokens=3),
            Usage(input_tokens=5, output_tokens=7),
        )

        self.assertEqual(result, Usage(input_tokens=7, output_tokens=10))

    def test_accumulate_summary_usage_preserves_optional_cache_tokens(self):
        result = accumulate_summary_usage(
            Usage(input_tokens=2, output_tokens=3, cache_read_tokens=4),
            Usage(input_tokens=5, output_tokens=7, cache_read_tokens=6, cache_write_tokens=8),
        )

        self.assertEqual(result.cache_read_tokens, 10)
        self.assertEqual(result.cache_write_tokens, 8)

    def test_accumulate_summary_usage_keeps_existing_usage_without_usage_data(self):
        accumulated_usage = Usage(input_tokens=2, output_tokens=3)

        result = accumulate_summary_usage(accumulated_usage, None)

        self.assertIs(result, accumulated_usage)

    def test_deep_summary_removes_tool_history_and_control_prompts_but_keeps_evidence(self):
        messages = [
            PromptMessage(role="system", content="【Fusion 身份一致性规则】保留", section_id=APP_IDENTITY),
            PromptMessage(
                role="system",
                content="【自主联网判断规则】按需调用工具",
                section_id=TOOL_USAGE_CONTRACT,
            ),
            PromptMessage(
                role="system",
                content="【执行计划控制规则】必须更新计划",
                section_id=AGENT_PLAN_CONTROL,
            ),
            PromptMessage(
                role="system",
                content="【深度研究执行约束】先建立计划",
                section_id=DEEP_RESEARCH_CONTRACT,
            ),
            PromptMessage(
                role="system",
                content="【本轮研究证据工作集】\n[2] status=read_success",
                section_id=RESEARCH_EVIDENCE_WORKSET,
            ),
            {"role": "assistant", "content": "", "tool_calls": [{"name": "url_read"}]},
            {"role": "tool", "content": "原始工具结果"},
            {"role": "user", "content": "原始研究问题"},
            {"role": "user", "content": "<web_context>已读来源安全投影</web_context>"},
        ]

        remove_conflicting_tool_usage_contract(messages, task_mode="deep_research")

        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "【Fusion 身份一致性规则】保留"},
                {"role": "system", "content": "【本轮研究证据工作集】\n[2] status=read_success"},
                {"role": "user", "content": "原始研究问题"},
                {"role": "user", "content": "<web_context>已读来源安全投影</web_context>"},
            ],
        )

    def test_standard_final_summary_keeps_context7_and_non_search_tool_transactions(self):
        messages = [
            {"role": "user", "content": "查询 FastAPI 文档并比较路线"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-context7",
                        "type": "function",
                        "function": {
                            "name": "mcp_context7_query",
                            "arguments": '{"libraryId":"/fastapi/fastapi","query":"routing"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-context7",
                "content": "Context7 文档结果",
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-route",
                        "type": "function",
                        "function": {
                            "name": "route_compare",
                            "arguments": '{"origin":"A","destination":"B"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-route",
                "content": "路线比较结果",
            },
        ]
        expected = [dict(message) for message in messages]

        remove_conflicting_tool_usage_contract(
            messages,
            task_mode="standard",
            final_synthesis=True,
        )

        self.assertEqual(messages, expected)

    def test_standard_final_summary_removes_skills_catalog(self):
        messages = [
            PromptMessage(
                role="system",
                content="Skills catalog: call load_skill before starting",
                section_id="skills_catalog",
            ),
            {"role": "user", "content": "请综合结论"},
        ]

        remove_conflicting_tool_usage_contract(
            messages,
            task_mode="standard",
            final_synthesis=True,
        )

        self.assertEqual(messages, [{"role": "user", "content": "请综合结论"}])

    def test_standard_final_summary_keeps_entire_parallel_mixed_tool_transaction(self):
        messages = [
            {"role": "user", "content": "同时搜索公告并查询官方库文档"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-search",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"官方公告"}'},
                    },
                    {
                        "id": "call-context7",
                        "type": "function",
                        "function": {
                            "name": "mcp_context7_query",
                            "arguments": '{"libraryId":"/fastapi/fastapi","query":"release notes"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-search",
                "content": "网页搜索结果",
            },
            {
                "role": "tool",
                "tool_call_id": "call-context7",
                "content": "Context7 文档结果",
            },
        ]
        expected = [dict(message) for message in messages]

        remove_conflicting_tool_usage_contract(
            messages,
            task_mode="standard",
            final_synthesis=True,
        )

        self.assertEqual(messages, expected)

    def test_append_summary_content_blocks_persists_only_final_text(self):
        content_blocks = []

        append_summary_content_blocks(
            content_blocks=content_blocks,
            content_buf="总结正文",
            text_block_id="blk-text",
        )

        self.assertEqual([block.type for block in content_blocks], ["text"])
        self.assertEqual(content_blocks[0].id, "blk-text")
        self.assertEqual(content_blocks[0].text, "总结正文")

    def test_append_summary_content_blocks_never_promotes_reasoning_to_answer(self):
        content_blocks = []

        append_summary_content_blocks(
            content_blocks=content_blocks,
            content_buf="",
            text_block_id="blk-text",
        )

        self.assertEqual(content_blocks, [])


class LimitSummaryStepTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _deferred_commit_request() -> LimitSummaryStepRequest:
        return LimitSummaryStepRequest(
            conversation_id="conv-deferred-terminal",
            task_id="task-deferred-terminal",
            run_id="run-deferred-terminal",
            step_number=1,
            model_id="gpt-4",
            provider="openai",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            messages=[{"role": "user", "content": "测试 deferred summary commit"}],
            should_use_reasoning=False,
            content_blocks=[],
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            emitter=AsyncMock(),
            session_cache=object(),
            total_timeout_s=300,
            run_start=0.0,
            start_step_fn=AsyncMock(),
            complete_step_fn=AsyncMock(),
            llm_call_fn=AsyncMock(),
            stream_round_fn=AsyncMock(),
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 1.0,
            defer_output=True,
        )

    async def test_summary_keeps_history_under_tool_ban_and_strips_it_only_for_retry(self):
        def transaction(call_id):
            return [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": "amap", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": "结果"},
            ]

        tools = [{"function": {"name": "web_search"}}]
        request = replace(
            self._deferred_commit_request(),
            call_kwargs={"tools": tools, "tool_choice": "auto", "temperature": 0.1},
            messages=[
                {"role": "user", "content": "q1"},
                *transaction("history-call"),
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
                *transaction("current-call"),
            ],
            history_tool_call_ids=frozenset({"history-call"}),
        )
        seen = []

        async def capture(**kwargs):
            seen.append(
                (
                    kwargs["call_kwargs"],
                    [message.get("tool_call_id") for message in kwargs["messages"] if message.get("role") == "tool"],
                )
            )

        with patch.object(limit_summary_module, "_call_limit_summary_round_once", side_effect=capture):
            for forbid_tools in (True, False):
                await limit_summary_module.call_limit_summary_round(
                    request=request,
                    thinking_block_id="t",
                    text_block_id="x",
                    step_id="s",
                    forbid_tools=forbid_tools,
                )

        self.assertEqual(
            seen,
            [
                ({"temperature": 0.1, "tools": tools, "tool_choice": "none"}, ["history-call", "current-call"]),
                ({"temperature": 0.1}, ["current-call"]),
            ],
        )

    async def test_summary_stream_failure_persists_visible_partial_round_detail(self):
        detail_scheduler = MagicMock()
        emitter = AsyncMock()
        observation = MagicMock()
        observation.wrap_response.side_effect = lambda response: response
        observation.finish_error = AsyncMock()
        context_plan = MagicMock(messages=[], estimated_tokens_after=10)
        context_plan.telemetry.return_value = {"context_management_status": "no_op"}

        async def stream_round_fn(*_args, partial_output, **_kwargs):
            partial_output["reasoning_buf"] = "总结部分推理"
            partial_output["content_buf"] = "总结部分回答"
            raise RuntimeError("summary stream failed")

        request = replace(
            self._deferred_commit_request(),
            emitter=emitter,
            llm_call_fn=AsyncMock(return_value="response"),
            stream_round_fn=stream_round_fn,
            llm_round_detail_scheduler=detail_scheduler,
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=AsyncMock(return_value=context_plan)),
            patch("app.services.stream.limit_summary.create_llm_round_observation", return_value=observation),
        ):
            with self.assertRaisesRegex(RuntimeError, "summary stream failed"):
                await limit_summary_module.call_limit_summary_round(
                    request=request,
                    thinking_block_id="thinking",
                    text_block_id="text",
                    step_id="step-summary",
                )

        emitter.llm_round_failed.assert_awaited_once()
        detail_scheduler.assert_called_once()
        draft = detail_scheduler.call_args.args[0]
        self.assertEqual(draft.reasoning_text, "总结部分推理")
        self.assertEqual(draft.content_text, "总结部分回答")

    async def test_deferred_summary_commit_terminal_secondary_preserves_primary_base_exception(self):
        error_factories = (
            ("runtime", lambda role: RuntimeError(f"{role} secret")),
            ("cancel", lambda role: asyncio.CancelledError(f"{role} secret")),
        )

        for primary_name, make_primary in error_factories:
            for secondary_name, make_secondary in error_factories:
                with self.subTest(primary=primary_name, secondary=secondary_name):
                    primary = make_primary("primary")
                    secondary = make_secondary("secondary")
                    finish_success = AsyncMock(side_effect=secondary)
                    lifecycle = SimpleNamespace(finish_success=finish_success)
                    round_result = limit_summary_module.LimitSummaryRoundResult(
                        reasoning_buf="",
                        content_buf="候选总结",
                        usage_data=Usage(input_tokens=1, output_tokens=1),
                        context=ContextUsage(status="no_op"),
                        llm_lifecycle=lifecycle,
                    )
                    complete_step = AsyncMock()
                    cancelling_before = asyncio.current_task().cancelling()

                    with (
                        patch(
                            "app.services.stream.limit_summary.append_limit_summary_prompt",
                        ),
                        patch(
                            "app.services.stream.limit_summary.start_limit_summary_step",
                            new=AsyncMock(
                                return_value=AgentStepContext(
                                    step_id="step-deferred-terminal",
                                    step_number=1,
                                    started_at=1.0,
                                    thinking_block_id="thinking-deferred-terminal",
                                    text_block_id="text-deferred-terminal",
                                )
                            ),
                        ),
                        patch(
                            "app.services.stream.limit_summary.run_summary_round_with_timeout",
                            new=AsyncMock(return_value=round_result),
                        ),
                        patch(
                            "app.services.stream.limit_summary._commit_limit_summary_result",
                            new=AsyncMock(side_effect=primary),
                        ),
                        patch(
                            "app.services.stream.limit_summary.complete_limit_summary_step",
                            new=complete_step,
                        ),
                        patch("app.services.stream.limit_summary.logger.warning") as warning,
                    ):
                        with self.assertRaises(type(primary)) as raised:
                            await run_limit_summary_step(request=self._deferred_commit_request())

                    self.assertIs(raised.exception, primary)
                    self.assertEqual(asyncio.current_task().cancelling(), cancelling_before)
                    finish_success.assert_awaited_once_with(output_visible=False)
                    complete_step.assert_not_awaited()
                    warning.assert_called_once()
                    logged = repr(warning.call_args)
                    self.assertIn("error_code=deferred_terminal_failure", logged)
                    self.assertIn(type(secondary).__name__, logged)
                    self.assertNotIn(str(primary), logged)
                    self.assertNotIn(str(secondary), logged)

    async def test_deferred_summary_commit_terminal_failure_without_primary_remains_fail_closed(self):
        for secondary in (RuntimeError("secondary secret"), asyncio.CancelledError("secondary secret")):
            with self.subTest(secondary=type(secondary).__name__):
                finish_success = AsyncMock(side_effect=secondary)
                lifecycle = SimpleNamespace(finish_success=finish_success)
                round_result = limit_summary_module.LimitSummaryRoundResult(
                    reasoning_buf="",
                    content_buf="候选总结",
                    usage_data=Usage(input_tokens=1, output_tokens=1),
                    context=ContextUsage(status="no_op"),
                    llm_lifecycle=lifecycle,
                )
                complete_step = AsyncMock()
                warning = MagicMock()
                cancelling_before = asyncio.current_task().cancelling()

                with (
                    patch(
                        "app.services.stream.limit_summary.append_limit_summary_prompt",
                    ),
                    patch(
                        "app.services.stream.limit_summary.start_limit_summary_step",
                        new=AsyncMock(
                            return_value=AgentStepContext(
                                step_id="step-deferred-terminal",
                                step_number=1,
                                started_at=1.0,
                                thinking_block_id="thinking-deferred-terminal",
                                text_block_id="text-deferred-terminal",
                            )
                        ),
                    ),
                    patch(
                        "app.services.stream.limit_summary.run_summary_round_with_timeout",
                        new=AsyncMock(return_value=round_result),
                    ),
                    patch(
                        "app.services.stream.limit_summary._commit_limit_summary_result",
                        new=AsyncMock(return_value=False),
                    ),
                    patch(
                        "app.services.stream.limit_summary.complete_limit_summary_step",
                        new=complete_step,
                    ),
                    patch("app.services.stream.limit_summary.logger.warning", new=warning),
                ):
                    with self.assertRaises(type(secondary)) as raised:
                        await run_limit_summary_step(request=self._deferred_commit_request())

                self.assertIs(raised.exception, secondary)
                self.assertEqual(asyncio.current_task().cancelling(), cancelling_before)
                finish_success.assert_awaited_once_with(output_visible=False)
                complete_step.assert_not_awaited()
                warning.assert_not_called()

    def _deep_request(
        self,
        *,
        workset,
        content_blocks,
        answer,
        model_id="gpt-4",
        reasoning_buf="",
    ):
        stream_kwargs = []
        answers = iter(answer if isinstance(answer, (list, tuple)) else [answer])

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-deep-summary",
                step_number=9,
                started_at=100.0,
                thinking_block_id="blk-deep-thinking",
                text_block_id="blk-deep-text",
            )

        async def prepare_context_fn(**kwargs):
            return ContextPlan(
                messages=list(kwargs["messages"]),
                status="no_op",
                context_window_tokens=1000,
                context_window_source="test",
                context_window_status="known",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
            )

        async def stream_round_fn(*_args, **kwargs):
            stream_kwargs.append(kwargs)
            return reasoning_buf, next(answers), [], "stop", Usage(input_tokens=2, output_tokens=3)

        emitter = AsyncMock()
        request = LimitSummaryStepRequest(
            conversation_id="conv-deep",
            task_id="task-deep",
            run_id="run-deep",
            step_number=9,
            model_id=model_id,
            provider="moonshot" if model_id == "kimi-k3" else "openai",
            litellm_model=f"moonshot/{model_id}" if model_id == "kimi-k3" else f"openai/{model_id}",
            litellm_kwargs={},
            messages=[{"role": "user", "content": "深度研究"}],
            should_use_reasoning=bool(reasoning_buf),
            content_blocks=content_blocks,
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=AsyncMock(return_value="response"),
            stream_round_fn=stream_round_fn,
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
            task_mode="deep_research",
            evidence_policy="deep_research_v1",
            research_workset=workset,
        )
        return request, emitter, stream_kwargs, prepare_context_fn

    async def test_deferred_real_tool_delta_emits_first_before_discard_completion(self):
        from app.ai.llm_round_observability import LLMRoundObservation, RoundMetadata

        workset, blocks = _deep_summary_evidence()
        request, emitter, _stream_kwargs, prepare_context_fn = self._deep_request(
            workset=workset,
            content_blocks=list(blocks),
            answer="unused",
        )
        events = []
        emitter.llm_round_started.side_effect = lambda **_kwargs: events.append("started")
        emitter.llm_round_first_output_delta.side_effect = lambda **_kwargs: events.append("first")
        emitter.llm_round_completed.side_effect = lambda **_kwargs: events.append("completed")
        now = [10.0]
        observation = LLMRoundObservation(
            metadata=RoundMetadata("conv", "run", 9, "step", "limit_summary", "model", "provider"),
            litellm_model="test/model",
            messages=[],
            call_kwargs={},
            clock=lambda: now[0],
            token_estimator=lambda *_args, **_kwargs: 1,
            context_window_resolver=lambda _model_id: (4096, "test", "known"),
            run_context_in_thread=False,
        )

        async def response():
            now[0] = 10.2
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="tool-1",
                                    function=SimpleNamespace(name="web_search", arguments="{}"),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=None,
            )

        request = replace(
            request,
            llm_call_fn=AsyncMock(return_value=response()),
            stream_round_fn=partial(llm_stream_module.stream_round, model_id="model"),
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary._create_limit_summary_observation", return_value=observation),
            patch("app.services.stream.llm_stream.check_lock_owner", new=AsyncMock(return_value=True)),
        ):
            result = await limit_summary_module.call_limit_summary_round(
                request=request,
                thinking_block_id="thinking",
                text_block_id="text",
                step_id="step",
            )
            await limit_summary_module._finish_summary_round_lifecycle(result, model_output_visible=False)

        emitter.llm_round_first_output_delta.assert_awaited_once()
        self.assertEqual(emitter.llm_round_first_output_delta.await_args.kwargs["delta_kind"], "tool_call")
        self.assertEqual(emitter.llm_round_first_output_delta.await_args.kwargs["ttft_ms"], 200)
        self.assertEqual(events, ["started", "first", "completed"])

    async def test_post_stream_summary_context_error_and_cancel_close_round(self):
        workset, blocks = _deep_summary_evidence()
        for primary in (
            RuntimeError("final summary context failed"),
            asyncio.CancelledError(),
            StreamOwnershipLostError("流已停止"),
        ):
            with self.subTest(primary=type(primary).__name__):
                request, emitter, _stream_kwargs, prepare_context_fn = self._deep_request(
                    workset=workset,
                    content_blocks=list(blocks),
                    answer="candidate",
                )

                def build_context(_plan, usage=None, **_kwargs):
                    if usage is None:
                        return ContextUsage(status="no_op")
                    raise primary

                with (
                    patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
                    patch("app.services.stream.limit_summary.build_context_usage", side_effect=build_context),
                ):
                    with self.assertRaises(type(primary)) as raised:
                        await limit_summary_module.call_limit_summary_round(
                            request=request,
                            thinking_block_id="thinking",
                            text_block_id="text",
                            step_id="step",
                        )

                self.assertIs(raised.exception, primary)
                if isinstance(primary, (asyncio.CancelledError, StreamOwnershipLostError)):
                    emitter.llm_round_cancelled.assert_awaited_once()
                else:
                    emitter.llm_round_failed.assert_awaited_once()

    async def test_deep_summary_defers_and_emits_valid_answer_once_with_used_evidence(self):
        workset, blocks = _deep_summary_evidence()
        request, emitter, stream_kwargs, prepare_context_fn = self._deep_request(
            workset=workset,
            content_blocks=blocks,
            answer="综合结论来自两个已读来源。[2][3]",
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertTrue(stream_kwargs[0]["defer_output"])
        self.assertFalse(outcome.incomplete)
        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], "综合结论来自两个已读来源。[2][3]")
        self.assertEqual(request.content_blocks[-1].text, "综合结论来自两个已读来源。[2][3]")
        self.assertEqual(emitter.evidence_item_upserted.await_count, 2)

    async def test_deep_summary_streams_and_persists_reasoning_for_k3_and_non_k3(self):
        for model_id in ("kimi-k3", "gpt-4"):
            with self.subTest(model_id=model_id):
                workset, blocks = _deep_summary_evidence()
                request, _emitter, stream_kwargs, prepare_context_fn = self._deep_request(
                    workset=workset,
                    content_blocks=blocks,
                    answer="综合结论来自两个已读来源。[2][3]",
                    model_id=model_id,
                    reasoning_buf="只供总结调用内部使用的推理",
                )

                with (
                    patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
                    patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
                ):
                    outcome = await run_limit_summary_step(request=request)

                self.assertFalse(outcome.incomplete)
                self.assertTrue(stream_kwargs[0]["allow_deferred_reasoning_output"])
                self.assertEqual(request.content_blocks[-1].type, "text")
                thinking_blocks = [block for block in request.content_blocks if block.type == "thinking"]
                self.assertEqual(len(thinking_blocks), 1)
                self.assertEqual(thinking_blocks[0].thinking, "只供总结调用内部使用的推理")

    async def test_deep_summary_replaces_invalid_or_insufficient_answer_with_deterministic_text(self):
        complete_workset, complete_blocks = _deep_summary_evidence()
        incomplete_workset = ResearchEvidenceWorkset()
        cases = ((incomplete_workset, [], "看似完成的结论。[2]"),)

        for workset, blocks, answer in cases:
            with self.subTest(answer=answer):
                request, emitter, _stream_kwargs, prepare_context_fn = self._deep_request(
                    workset=workset,
                    content_blocks=list(blocks),
                    answer=answer,
                )
                with (
                    patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
                    patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
                ):
                    outcome = await run_limit_summary_step(request=request)

                self.assertTrue(outcome.incomplete)
                append_chunk.assert_awaited_once()
                self.assertEqual(append_chunk.await_args.args[2], DEEP_RESEARCH_INCOMPLETE_TEXT)
                self.assertEqual(request.content_blocks[-1].text, DEEP_RESEARCH_INCOMPLETE_TEXT)
                emitter.evidence_item_upserted.assert_not_awaited()

    async def test_deep_summary_repairs_missing_or_invalid_citation_once_before_emitting(self):
        cases = (
            ("缺少引用的完整结论。", "补齐引用后的完整结论。[2][3]"),
            ("使用了错误编号。[99]", "改用已读来源编号后的完整结论。[2][3]"),
        )

        for invalid_answer, repaired_answer in cases:
            with self.subTest(invalid_answer=invalid_answer):
                workset, blocks = _deep_summary_evidence()
                request, emitter, stream_kwargs, prepare_context_fn = self._deep_request(
                    workset=workset,
                    content_blocks=list(blocks),
                    answer=[invalid_answer, repaired_answer],
                )
                warnings = []
                request = LimitSummaryStepRequest(
                    **{
                        **request.__dict__,
                        "warning_fn": warnings.append,
                    }
                )
                with (
                    patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
                    patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
                ):
                    outcome = await run_limit_summary_step(request=request)

                self.assertFalse(outcome.incomplete)
                self.assertEqual(len(stream_kwargs), 2)
                self.assertEqual(request.llm_call_fn.await_count, 2)
                sent_snapshots = [call.args[2] for call in request.llm_call_fn.await_args_list]
                self.assertTrue(
                    all(
                        sum(str(item.get("content") or "").count(VISIBLE_RESPONSE_LANGUAGE_PROMPT) for item in snapshot)
                        == 1
                        for snapshot in sent_snapshots
                    )
                )
                self.assertTrue(
                    any(
                        "[Deep-research completion validation]" in str(message.get("content") or "")
                        for message in sent_snapshots[1]
                    )
                )
                self.assertTrue(sent_snapshots[1][-1]["content"].endswith(VISIBLE_RESPONSE_LANGUAGE_PROMPT))
                self.assertFalse(
                    any(VISIBLE_RESPONSE_LANGUAGE_PROMPT in str(item.get("content") or "") for item in request.messages)
                )
                append_chunk.assert_awaited_once()
                self.assertEqual(append_chunk.await_args.args[2], repaired_answer)
                self.assertEqual(request.content_blocks[-1].text, repaired_answer)
                self.assertEqual(emitter.evidence_item_upserted.await_count, 2)
                self.assertTrue(any("引用校验未通过" in warning for warning in warnings))
                self.assertTrue(
                    any(
                        "[Deep-research completion validation]" in str(message.get("content", ""))
                        for message in request.messages
                    )
                )

    async def test_deep_summary_keeps_deterministic_failure_when_citation_repair_still_invalid(self):
        workset, blocks = _deep_summary_evidence()
        request, emitter, stream_kwargs, prepare_context_fn = self._deep_request(
            workset=workset,
            content_blocks=list(blocks),
            answer=["伪造引用。[99]", "仍然伪造引用。[98]"],
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertTrue(outcome.incomplete)
        self.assertEqual(len(stream_kwargs), 2)
        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], DEEP_RESEARCH_INCOMPLETE_TEXT)
        self.assertEqual(request.content_blocks[-1].text, DEEP_RESEARCH_INCOMPLETE_TEXT)
        emitter.evidence_item_upserted.assert_not_awaited()

    async def test_deep_summary_closes_malformed_citation_repair_round(self):
        workset, blocks = _deep_summary_evidence()
        request, emitter, _stream_kwargs, prepare_context_fn = self._deep_request(
            workset=workset,
            content_blocks=list(blocks),
            answer="未引用的候选",
        )
        request = replace(
            request,
            stream_round_fn=AsyncMock(
                side_effect=[
                    ("", "未引用的候选", [], "stop", Usage(input_tokens=1, output_tokens=1)),
                    ("隐藏协议", "", [], "tool_protocol_error", Usage(input_tokens=1, output_tokens=1)),
                ]
            ),
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertTrue(outcome.incomplete)
        self.assertEqual(emitter.llm_round_completed.await_count, 2)
        self.assertTrue(all(call.kwargs["ttft_ms"] is None for call in emitter.llm_round_completed.await_args_list))

    async def test_malformed_summary_retry_exception_closes_both_rounds(self):
        workset, blocks = _deep_summary_evidence()
        request, emitter, _stream_kwargs, prepare_context_fn = self._deep_request(
            workset=workset,
            content_blocks=list(blocks),
            answer="unused",
        )
        primary = RuntimeError("retry provider failed")
        request = replace(
            request,
            task_mode="standard",
            stream_round_fn=AsyncMock(
                side_effect=[
                    ("隐藏协议", "", [], "tool_protocol_error", Usage(input_tokens=1, output_tokens=1)),
                    primary,
                ]
            ),
        )

        with patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn):
            with self.assertRaises(RuntimeError) as raised:
                await run_limit_summary_step(request=request)

        self.assertIs(raised.exception, primary)
        self.assertEqual(emitter.llm_round_completed.await_count, 1)
        self.assertIsNone(emitter.llm_round_completed.await_args.kwargs["ttft_ms"])
        emitter.llm_round_failed.assert_awaited_once()

    async def test_no_progress_summary_removes_conflicting_tool_usage_contract_before_call(self):
        sent_messages = []

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=3,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def prepare_context_fn(**kwargs):
            return ContextPlan(
                messages=list(kwargs["messages"]),
                status="no_op",
                context_window_tokens=1000,
                context_window_source="test",
                context_window_status="known",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
            )

        async def llm_call_fn(_model, _kwargs, messages, **_call_kwargs):
            sent_messages.extend(messages)
            return "response"

        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=3,
            model_id="deepseek-chat",
            provider="deepseek",
            litellm_model="deepseek/deepseek-v4-flash",
            litellm_kwargs={},
            messages=[
                PromptMessage(
                    role="system",
                    content="【工具调用一致性规则】需要联网时必须调用 web_search。",
                    section_id=TOOL_USAGE_CONTRACT,
                ),
                {"role": "user", "content": "北京周末天气如何？"},
            ],
            should_use_reasoning=True,
            content_blocks=[],
            call_kwargs={"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"},
            accumulated_usage=Usage(input_tokens=0, output_tokens=0),
            emitter=AsyncMock(),
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=llm_call_fn,
            stream_round_fn=AsyncMock(return_value=("", "最终答复", [], "stop", None)),
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
            summary_finish_reason="no_progress_summary",
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            await run_limit_summary_step(request=request)

        system_text = "\n".join(
            str(message.get("content", "")) for message in sent_messages if message.get("role") == "system"
        )
        self.assertNotIn("【工具调用一致性规则】", system_text)
        self.assertIn("searches are no longer producing useful new information", system_text)

    async def test_no_progress_summary_retries_once_when_model_returns_tool_protocol(self):
        content_blocks = []
        warnings = []
        emitter = AsyncMock()

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=3,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def prepare_context_fn(**kwargs):
            return ContextPlan(
                messages=list(kwargs["messages"]),
                status="no_op",
                context_window_tokens=1000,
                context_window_source="test",
                context_window_status="known",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
            )

        stream_round_fn = AsyncMock(
            side_effect=[
                (
                    "内部工具规划",
                    "这段工具协议前缀不得展示",
                    [],
                    "tool_protocol_error",
                    Usage(input_tokens=5, output_tokens=7),
                ),
                ("最终总结推理", "最终答复", [], "stop", Usage(input_tokens=3, output_tokens=4)),
            ]
        )
        sent_snapshots = []
        sent_tool_choices = []

        async def llm_call_fn(_model, _kwargs, call_messages, **_call_kwargs):
            sent_snapshots.append([dict(message) for message in call_messages])
            sent_tool_choices.append(_call_kwargs.get("tool_choice"))
            return f"response-{len(sent_snapshots)}"

        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=3,
            model_id="deepseek-chat",
            provider="deepseek",
            litellm_model="deepseek/deepseek-v4-flash",
            litellm_kwargs={},
            messages=[{"role": "user", "content": "北京周末天气如何？"}],
            should_use_reasoning=True,
            content_blocks=content_blocks,
            call_kwargs={"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"},
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=llm_call_fn,
            stream_round_fn=stream_round_fn,
            log_round_summary_fn=lambda **_kwargs: None,
            warning_fn=warnings.append,
            clock=lambda: 120.0,
            summary_finish_reason="no_progress_summary",
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertEqual(len(sent_snapshots), 2)
        self.assertTrue(
            all(
                sum(str(item.get("content") or "").count(VISIBLE_RESPONSE_LANGUAGE_PROMPT) for item in snapshot) == 1
                for snapshot in sent_snapshots
            )
        )
        self.assertIn("Do not output tool calls", sent_snapshots[1][-1]["content"])
        # 首次总结带工具定义并禁止调用；模型无视禁令后，重做不再带工具定义。
        self.assertEqual(sent_tool_choices, ["none", None])
        self.assertTrue(sent_snapshots[1][-1]["content"].endswith(VISIBLE_RESPONSE_LANGUAGE_PROMPT))
        self.assertFalse(
            any(VISIBLE_RESPONSE_LANGUAGE_PROMPT in str(item.get("content") or "") for item in request.messages)
        )
        self.assertTrue(all(call.kwargs["defer_output"] for call in stream_round_fn.await_args_list))
        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], "最终答复")
        self.assertEqual(outcome.accumulated_usage, Usage(input_tokens=10, output_tokens=14))
        self.assertEqual([block.type for block in content_blocks], ["thinking", "text"])
        self.assertEqual(content_blocks[0].thinking, "内部工具规划最终总结推理")
        self.assertEqual(content_blocks[1].text, "最终答复")
        self.assertNotIn("工具协议前缀", content_blocks[1].text)
        self.assertTrue(any("工具协议" in warning for warning in warnings))
        self.assertTrue(
            any("Do not output tool calls" in str(message.get("content", "")) for message in request.messages)
        )
        self.assertEqual(emitter.llm_round_completed.await_count, 2)
        self.assertIsNone(emitter.llm_round_completed.await_args_list[0].kwargs["ttft_ms"])

    async def test_summary_blocked_by_provider_content_review_becomes_filtered_notice(self):
        class _ProviderError(Exception):
            status_code = 400

        refusal = "The request was rejected because it was considered high risk"
        for name, stream_round_fn in (
            ("refusal_reply", AsyncMock(return_value=("", refusal, [], "content_filter", None))),
            ("error", AsyncMock(side_effect=_ProviderError("InternalError.Algo.DataInspectionFailed"))),
        ):
            with self.subTest(name):
                content_blocks = [{"type": "search", "status": "success"}]
                emitter = AsyncMock()

                async def start_step_fn(**_kwargs):
                    return AgentStepContext(
                        step_id="step-summary",
                        step_number=3,
                        started_at=100.0,
                        thinking_block_id="blk-thinking",
                        text_block_id="blk-text",
                    )

                async def prepare_context_fn(**kwargs):
                    return ContextPlan(
                        messages=list(kwargs["messages"]),
                        status="no_op",
                        context_window_tokens=1000,
                        context_window_source="test",
                        context_window_status="known",
                        estimated_tokens_before=100,
                        estimated_tokens_after=100,
                    )

                request = LimitSummaryStepRequest(
                    conversation_id="conv-1",
                    task_id="task-1",
                    run_id="run-1",
                    step_number=3,
                    model_id="mimo-v2.6-pro",
                    provider="xiaomi",
                    litellm_model="openai/mimo-v2.6-pro",
                    litellm_kwargs={},
                    messages=[{"role": "user", "content": "今天国际新闻有啥大事？"}],
                    should_use_reasoning=False,
                    content_blocks=content_blocks,
                    call_kwargs={},
                    accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                    emitter=emitter,
                    session_cache=object(),
                    total_timeout_s=300,
                    run_start=100.0,
                    start_step_fn=start_step_fn,
                    complete_step_fn=AsyncMock(),
                    llm_call_fn=AsyncMock(return_value="response"),
                    stream_round_fn=stream_round_fn,
                    log_round_summary_fn=lambda **_kwargs: None,
                    clock=lambda: 120.0,
                    summary_finish_reason="limit_reached",
                )

                with (
                    patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
                    patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
                ):
                    await run_limit_summary_step(request=request)

                self.assertEqual([block.type for block in content_blocks], ["content_filtered"])
                self.assertNotIn(refusal, [call.args[2] for call in append_chunk.await_args_list])
                emitter.content_block_upserted.assert_awaited_once()
                emitter.llm_round_failed.assert_not_awaited()
                completed = emitter.llm_round_completed.await_args.kwargs
                self.assertEqual(completed["output_provenance"]["reason"], "content_filtered")

    async def test_no_progress_summary_uses_safe_fallback_after_repeated_tool_protocol(self):
        content_blocks = []
        emitter = AsyncMock()

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=3,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def prepare_context_fn(**kwargs):
            return ContextPlan(
                messages=list(kwargs["messages"]),
                status="no_op",
                context_window_tokens=1000,
                context_window_source="test",
                context_window_status="known",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
            )

        tool_protocol_result = (
            "内部工具规划",
            "",
            [],
            "tool_protocol_error",
            None,
        )
        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=3,
            model_id="deepseek-chat",
            provider="deepseek",
            litellm_model="deepseek/deepseek-v4-flash",
            litellm_kwargs={},
            messages=[{"role": "user", "content": "北京周末天气如何？"}],
            should_use_reasoning=True,
            content_blocks=content_blocks,
            call_kwargs={"tools": [{"function": {"name": "web_search"}}], "tool_choice": "auto"},
            accumulated_usage=Usage(input_tokens=0, output_tokens=0),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=AsyncMock(side_effect=["response-1", "response-2"]),
            stream_round_fn=AsyncMock(side_effect=[tool_protocol_result, tool_protocol_result]),
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
            summary_finish_reason="no_progress_summary",
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
        ):
            outcome = await run_limit_summary_step(request=request)

        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], SUMMARY_PROTOCOL_FALLBACK_TEXT)
        self.assertTrue(outcome.incomplete)
        self.assertEqual([block.type for block in content_blocks], ["thinking", "text"])
        self.assertEqual(content_blocks[0].thinking, "内部工具规划内部工具规划")
        self.assertIn("未能生成可靠的最终答复", content_blocks[1].text)
        self.assertNotIn("DSML", content_blocks[1].text)
        self.assertEqual(emitter.llm_round_completed.await_count, 2)
        self.assertTrue(all(call.kwargs["ttft_ms"] is None for call in emitter.llm_round_completed.await_args_list))

    async def test_limit_summary_replaces_previous_round_context_with_final_summary_round(self):
        emitter = AsyncMock()
        context_updates = []

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=4,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=4,
            model_id="gpt-4",
            provider="openai",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            messages=[],
            should_use_reasoning=False,
            content_blocks=[],
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=100, output_tokens=20),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=AsyncMock(return_value="response"),
            stream_round_fn=AsyncMock(return_value=("", "总结", [], "stop", Usage(input_tokens=70, output_tokens=10))),
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
            on_context_updated=context_updates.append,
        )
        plan = ContextPlan(
            messages=[{"role": "system", "content": "总结"}],
            status="trimmed",
            context_window_tokens=1000,
            context_window_source="registry",
            context_window_status="known",
            estimated_tokens_before=900,
            estimated_tokens_after=700,
            removed_turns=1,
            removed_messages=2,
        )

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=AsyncMock(return_value=plan)),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertEqual(outcome.accumulated_usage, Usage(input_tokens=170, output_tokens=30))
        self.assertEqual(
            outcome.context,
            ContextUsage(
                status="trimmed",
                round_index=4,
                window_tokens=1000,
                estimated_tokens_before=900,
                estimated_tokens_after=700,
                actual_prompt_tokens=70,
                removed_turns=1,
                removed_messages=2,
            ),
        )
        self.assertEqual(context_updates[-1], outcome.context)
        self.assertEqual(emitter.context_status_updated.await_args_list[-1].kwargs["phase"], "final")

    async def test_limit_summary_uses_real_budgeted_snapshot_and_keeps_tail_system_prompt(self):
        emitter = AsyncMock()
        messages = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": "b" * 100},
            {"role": "user", "content": "最新问题"},
        ]
        sent_messages = []

        def estimator(_model, candidate, _kwargs):
            return sum(
                min(len(message.get("content") or ""), 10)
                if message.get("role") == "system"
                else len(message.get("content") or "")
                for message in candidate
            )

        async def budgeted_prepare(**kwargs):
            return await prepare_context_real(
                **kwargs,
                window_resolver=lambda _model: (100, "test", "known"),
                token_estimator=estimator,
                run_in_thread=False,
                use_fast_path=False,
            )

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=2,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def llm_call_fn(_model, _kwargs, call_messages, **_call_kwargs):
            sent_messages.extend(call_messages)
            return "response"

        async def stream_round_fn(*_args, **_kwargs):
            return "", "总结", [], "stop", None

        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=2,
            model_id="gpt-4",
            provider="openai",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            messages=messages,
            should_use_reasoning=False,
            content_blocks=[],
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=0, output_tokens=0),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=llm_call_fn,
            stream_round_fn=stream_round_fn,
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
        )
        observation = MagicMock()
        observation.finish_success = AsyncMock()
        observation.finish_error = AsyncMock()
        observation.wrap_response.side_effect = lambda response: response

        with (
            patch("app.services.stream.limit_summary.prepare_context", new=budgeted_prepare),
            patch(
                "app.services.stream.limit_summary.create_llm_round_observation",
                return_value=observation,
            ),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            await run_limit_summary_step(request=request)

        self.assertEqual([message["role"] for message in sent_messages], ["user", "system"])
        self.assertEqual(sent_messages[0]["content"], "最新问题")
        self.assertTrue(sent_messages[-1]["content"].startswith(LIMIT_SUMMARY_PROMPT))
        self.assertTrue(sent_messages[-1]["content"].endswith(VISIBLE_RESPONSE_LANGUAGE_PROMPT))
        self.assertEqual(
            sum(str(item.get("content") or "").count(VISIBLE_RESPONSE_LANGUAGE_PROMPT) for item in sent_messages),
            1,
        )
        self.assertEqual(len(messages), 4)
        self.assertTrue(messages[-1]["content"].startswith(LIMIT_SUMMARY_PROMPT))
        self.assertIn(NO_TOOL_EVIDENCE_SUMMARY_PROMPT, messages[-1]["content"])

        system_messages = [dict(message) for message in sent_messages if message["role"] == "system"]
        expected_fingerprint = hashlib.sha256(
            json.dumps(system_messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(emitter.llm_round_started.await_args.kwargs["system_prompt_fingerprint"], expected_fingerprint)

    async def test_limit_summary_records_independent_round_observation(self):
        messages = []
        observation = MagicMock()
        log_round_summary_fn = MagicMock()
        emitter = AsyncMock()
        call_order = []
        emitter.llm_round_started.side_effect = lambda **_kwargs: call_order.append("started")
        emitter.llm_round_cancelled.side_effect = lambda **_kwargs: call_order.append("cancelled")
        observation.finish_success = AsyncMock()
        observation.finish_error = AsyncMock()
        observation.wrap_response.side_effect = lambda response: response

        async def llm_call_fn(*_args, **_kwargs):
            call_order.append("network")
            return "response"

        async def stream_round_fn(*_args, **_kwargs):
            return "", "总结", [], "cancelled", Usage(input_tokens=5, output_tokens=7)

        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-summary",
                step_number=2,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        request = LimitSummaryStepRequest(
            conversation_id="conv-1",
            task_id="task-1",
            run_id="run-1",
            step_number=2,
            model_id="gpt-4",
            provider="openai",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            messages=messages,
            should_use_reasoning=False,
            content_blocks=[],
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=2, output_tokens=3),
            emitter=emitter,
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=llm_call_fn,
            stream_round_fn=stream_round_fn,
            log_round_summary_fn=log_round_summary_fn,
            clock=lambda: 120.0,
            summary_finish_reason="no_progress_summary",
        )

        context_plan = MagicMock(
            messages=[{"role": "system", "content": LIMIT_SUMMARY_PROMPT}],
            estimated_tokens_after=12,
        )
        context_plan.telemetry.return_value = {"context_management_status": "trimmed"}
        with (
            patch(
                "app.services.stream.limit_summary.create_llm_round_observation",
                return_value=observation,
            ) as create_observation,
            patch(
                "app.services.stream.limit_summary.prepare_context",
                new=AsyncMock(return_value=context_plan),
            ),
        ):
            outcome = await run_limit_summary_step(request=request)

        self.assertEqual(outcome.accumulated_usage, Usage(input_tokens=7, output_tokens=10))
        self.assertEqual(create_observation.call_args.kwargs["round_kind"], "limit_summary")
        self.assertEqual(create_observation.call_args.kwargs["round_index"], 2)
        self.assertEqual(create_observation.call_args.kwargs["messages"], context_plan.messages)
        self.assertEqual(
            create_observation.call_args.kwargs["context_management"],
            {"context_management_status": "trimmed"},
        )
        observation.start.assert_called_once_with()
        observation.finish_success.assert_awaited_once_with(
            usage=Usage(input_tokens=5, output_tokens=7),
            finish_reason="cancelled",
        )
        self.assertEqual(log_round_summary_fn.call_args.kwargs["finish_reason"], "no_progress_summary")
        self.assertEqual(call_order, ["started", "network", "cancelled"])
        emitter.llm_round_completed.assert_not_awaited()

    async def test_run_limit_summary_step_appends_prompt_and_records_success(self):
        messages = [{"role": "user", "content": "hi"}]
        content_blocks = []
        accumulated_usage = Usage(input_tokens=2, output_tokens=3)
        emitter = AsyncMock()
        session_cache = object()
        events = []
        emitter.llm_round_started.side_effect = lambda **_kwargs: events.append(("llm_started",))
        emitter.llm_round_first_output_delta.side_effect = lambda **_kwargs: events.append(("first",))
        emitter.llm_round_completed.side_effect = lambda **_kwargs: events.append(("llm_completed",))

        async def start_step_fn(*, emitter, session_cache, run_id, step_number, clock, on_step_started):
            events.append(("start", emitter, session_cache, run_id, step_number, clock))
            on_step_started("step-summary")
            return AgentStepContext(
                step_id="step-summary",
                step_number=step_number,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def llm_call_fn(litellm_model, litellm_kwargs, call_messages, **call_kwargs):
            events.append(("llm", litellm_model, litellm_kwargs, list(call_messages), call_kwargs))
            return "response"

        async def stream_round_fn(
            response,
            conversation_id,
            task_id,
            should_use_reasoning,
            thinking_block_id,
            text_block_id,
            *,
            run_id,
            step_id,
            defer_output,
        ):
            events.append(
                (
                    "stream",
                    response,
                    conversation_id,
                    task_id,
                    should_use_reasoning,
                    thinking_block_id,
                    text_block_id,
                    run_id,
                    step_id,
                    defer_output,
                )
            )
            return "推理", "总结正文", [], "stop", Usage(input_tokens=5, output_tokens=7)

        def log_round_summary_fn(**kwargs):
            events.append(("log", kwargs))

        async def complete_step_fn(*, context, emitter, session_cache, tool_names, tool_call_count, clock):
            events.append(("complete", context, emitter, session_cache, tool_names, tool_call_count, clock))

        warnings = []
        marked_step_ids = []

        def clock():
            return 120.0

        observation = MagicMock(first_output_delta_kind="content", first_output_delta_ms=50, duration_ms=80)
        observation.finish_success = AsyncMock()
        observation.finish_error = AsyncMock()
        observation.wrap_response.side_effect = lambda response: response
        append_chunk = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append(("answer_chunk",)))
        with (
            patch("app.services.stream.limit_summary.append_chunk", new=append_chunk),
            patch(
                "app.services.stream.limit_summary.create_llm_round_observation",
                return_value=observation,
            ),
        ):
            outcome = await run_limit_summary_step(
                request=LimitSummaryStepRequest(
                    conversation_id="conv-1",
                    task_id="task-1",
                    run_id="run-1",
                    step_number=4,
                    model_id="gpt-4",
                    provider="openai",
                    litellm_model="openai/gpt-4",
                    litellm_kwargs={"metadata": {"trace": "x"}},
                    messages=messages,
                    should_use_reasoning=True,
                    content_blocks=content_blocks,
                    call_kwargs={
                        "tools": [{"function": {"name": "web_search"}}],
                        "tool_choice": "auto",
                        "temperature": 0.1,
                    },
                    accumulated_usage=accumulated_usage,
                    emitter=emitter,
                    session_cache=session_cache,
                    total_timeout_s=300,
                    run_start=100.0,
                    start_step_fn=start_step_fn,
                    complete_step_fn=complete_step_fn,
                    llm_call_fn=llm_call_fn,
                    stream_round_fn=stream_round_fn,
                    log_round_summary_fn=log_round_summary_fn,
                    warning_fn=warnings.append,
                    clock=clock,
                    on_step_started=marked_step_ids.append,
                ),
            )

        self.assertEqual(marked_step_ids, ["step-summary"])
        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], "总结正文")
        self.assertFalse(outcome.incomplete)
        self.assertEqual(messages[-1]["role"], "system")
        self.assertTrue(messages[-1]["content"].startswith(LIMIT_SUMMARY_PROMPT))
        self.assertIn(NO_TOOL_EVIDENCE_SUMMARY_PROMPT, messages[-1]["content"])
        self.assertEqual(outcome.accumulated_usage, Usage(input_tokens=7, output_tokens=10))
        self.assertEqual(len(content_blocks), 2)
        self.assertEqual(content_blocks[0].type, "thinking")
        self.assertEqual(content_blocks[0].id, "blk-thinking")
        self.assertEqual(content_blocks[0].thinking, "推理")
        self.assertEqual(content_blocks[1].type, "text")
        self.assertEqual(content_blocks[1].id, "blk-text")
        self.assertEqual(content_blocks[1].text, "总结正文")
        self.assertEqual(warnings, [])

        self.assertEqual(
            [event[0] for event in events],
            [
                "start",
                "llm_started",
                "llm",
                "stream",
                "log",
                "answer_chunk",
                "first",
                "llm_completed",
                "complete",
            ],
        )
        self.assertEqual(
            events[2][4],
            {"temperature": 0.1, "tools": [{"function": {"name": "web_search"}}], "tool_choice": "none"},
        )
        self.assertEqual(
            events[3],
            (
                "stream",
                "response",
                "conv-1",
                "task-1",
                True,
                "blk-thinking",
                "blk-text",
                "run-1",
                "step-summary",
                True,
            ),
        )
        self.assertEqual(events[4][1]["finish_reason"], "limit_summary")
        self.assertEqual(events[4][1]["tool_calls_count"], 0)
        self.assertEqual(events[4][1]["reasoning_buf"], "推理")
        self.assertEqual(events[4][1]["content_buf"], "总结正文")
        self.assertEqual(events[8][1].step_id, "step-summary")
        self.assertEqual(events[8][4], [])
        self.assertEqual(events[8][5], 0)

    async def test_run_limit_summary_step_swallows_timeout_and_completes_step(self):
        messages = [{"role": "user", "content": "hi"}]
        content_blocks = []
        accumulated_usage = Usage(input_tokens=2, output_tokens=3)
        emitter = object()
        session_cache = object()
        events = []

        async def start_step_fn(*, emitter, session_cache, run_id, step_number, clock, on_step_started):
            on_step_started("step-timeout")
            events.append(("start", step_number))
            return AgentStepContext(
                step_id="step-timeout",
                step_number=step_number,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def llm_call_fn(*_args, **_kwargs):
            events.append(("llm",))
            raise asyncio.TimeoutError

        async def stream_round_fn(*_args, **_kwargs):
            events.append(("stream",))
            return "", "", [], "stop", None

        def log_round_summary_fn(**kwargs):
            events.append(("log", kwargs))

        async def complete_step_fn(*, context, emitter, session_cache, tool_names, tool_call_count, clock):
            events.append(("complete", context.step_id, tool_names, tool_call_count))

        warnings = []
        marked_step_ids = []

        with patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk:
            outcome = await run_limit_summary_step(
                request=LimitSummaryStepRequest(
                    conversation_id="conv-1",
                    task_id="task-1",
                    run_id="run-1",
                    step_number=5,
                    model_id="gpt-4",
                    provider="openai",
                    litellm_model="openai/gpt-4",
                    litellm_kwargs={},
                    messages=messages,
                    should_use_reasoning=False,
                    content_blocks=content_blocks,
                    call_kwargs={"tools": [], "tool_choice": "auto"},
                    accumulated_usage=accumulated_usage,
                    emitter=emitter,
                    session_cache=session_cache,
                    total_timeout_s=2,
                    run_start=0.0,
                    start_step_fn=start_step_fn,
                    complete_step_fn=complete_step_fn,
                    llm_call_fn=llm_call_fn,
                    stream_round_fn=stream_round_fn,
                    log_round_summary_fn=log_round_summary_fn,
                    warning_fn=warnings.append,
                    clock=lambda: 10.0,
                    on_step_started=marked_step_ids.append,
                ),
            )

        self.assertEqual(marked_step_ids, ["step-timeout"])
        append_chunk.assert_awaited_once()
        self.assertEqual(append_chunk.await_args.args[2], SUMMARY_PROTOCOL_FALLBACK_TEXT)
        self.assertTrue(outcome.incomplete)
        self.assertEqual(messages[-1]["role"], "system")
        self.assertTrue(messages[-1]["content"].startswith(LIMIT_SUMMARY_PROMPT))
        self.assertIn(NO_TOOL_EVIDENCE_SUMMARY_PROMPT, messages[-1]["content"])
        self.assertEqual(outcome.accumulated_usage, accumulated_usage)
        self.assertEqual([block.text for block in content_blocks], [SUMMARY_PROTOCOL_FALLBACK_TEXT])
        self.assertEqual([event[0] for event in events], ["start", "llm", "complete"])
        self.assertEqual(events[-1], ("complete", "step-timeout", [], 0))
        self.assertEqual(len(warnings), 1)
        self.assertIn("触顶总结超出剩余预算", warnings[0])
        self.assertIn("conv_id=conv-1", warnings[0])

    async def test_run_limit_summary_step_reraises_non_timeout_error(self):
        messages = [{"role": "user", "content": "hi"}]
        content_blocks = []
        events = []

        async def start_step_fn(*, emitter, session_cache, run_id, step_number, clock, on_step_started):
            on_step_started("step-error")
            events.append(("start", step_number))
            return AgentStepContext(
                step_id="step-error",
                step_number=step_number,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def llm_call_fn(*_args, **_kwargs):
            events.append(("llm",))
            raise RuntimeError("upstream LLM 5xx")

        async def stream_round_fn(*_args, **_kwargs):
            events.append(("stream",))
            return "", "", [], "stop", None

        def log_round_summary_fn(**kwargs):
            events.append(("log", kwargs))

        async def complete_step_fn(**_kwargs):
            events.append(("complete",))

        with self.assertRaises(RuntimeError) as cm:
            await run_limit_summary_step(
                request=LimitSummaryStepRequest(
                    conversation_id="conv-1",
                    task_id="task-1",
                    run_id="run-1",
                    step_number=6,
                    model_id="gpt-4",
                    provider="openai",
                    litellm_model="openai/gpt-4",
                    litellm_kwargs={},
                    messages=messages,
                    should_use_reasoning=False,
                    content_blocks=content_blocks,
                    call_kwargs={"tools": [], "tool_choice": "auto"},
                    accumulated_usage=Usage(input_tokens=2, output_tokens=3),
                    emitter=object(),
                    session_cache=object(),
                    total_timeout_s=300,
                    run_start=0.0,
                    start_step_fn=start_step_fn,
                    complete_step_fn=complete_step_fn,
                    llm_call_fn=llm_call_fn,
                    stream_round_fn=stream_round_fn,
                    log_round_summary_fn=log_round_summary_fn,
                    warning_fn=None,
                    clock=lambda: 10.0,
                    on_step_started=lambda _step_id: None,
                ),
            )

        self.assertIn("upstream LLM 5xx", str(cm.exception))
        self.assertEqual([event[0] for event in events], ["start", "llm"])
        self.assertEqual(content_blocks, [])


class LimitSummaryNoEvidenceFactBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """issue #30 P1-B：0 次工具调用触顶时不得输出具体班次/价格/时长。"""

    def _standard_request(self, *, answer: str, content_blocks: list, user_message: str):
        async def start_step_fn(**_kwargs):
            return AgentStepContext(
                step_id="step-limit-summary",
                step_number=9,
                started_at=100.0,
                thinking_block_id="blk-thinking",
                text_block_id="blk-text",
            )

        async def prepare_context_fn(**kwargs):
            return ContextPlan(
                messages=list(kwargs["messages"]),
                status="no_op",
                context_window_tokens=1000,
                context_window_source="test",
                context_window_status="known",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
            )

        async def stream_round_fn(*_args, **_kwargs):
            return "", answer, [], "stop", Usage(input_tokens=2, output_tokens=3)

        request = LimitSummaryStepRequest(
            conversation_id="conv-limit",
            task_id="task-limit",
            run_id="run-limit",
            step_number=9,
            model_id="gpt-4",
            provider="openai",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            messages=[{"role": "user", "content": user_message}],
            should_use_reasoning=False,
            content_blocks=content_blocks,
            recovery_evidence=_summary_recovery_evidence(),
            call_kwargs={},
            accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            emitter=AsyncMock(),
            session_cache=object(),
            total_timeout_s=300,
            run_start=100.0,
            start_step_fn=start_step_fn,
            complete_step_fn=AsyncMock(),
            llm_call_fn=AsyncMock(return_value="response"),
            stream_round_fn=stream_round_fn,
            log_round_summary_fn=lambda **_kwargs: None,
            clock=lambda: 120.0,
        )
        return request, prepare_context_fn

    async def _run(
        self, *, answer: str, content_blocks: list, user_message: str = "国庆想带父母从武汉去桂林玩，怎么过去省心一些？"
    ):
        request, prepare_context_fn = self._standard_request(
            answer=answer,
            content_blocks=content_blocks,
            user_message=user_message,
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append_chunk,
        ):
            await run_limit_summary_step(request=request)
        return request, append_chunk

    async def test_没有能力快照时不按回答措辞改写(self):
        """事实需求只来自冻结能力（见下方冻结外部事实能力用例），不用正则解析回答里的班次价格时长。"""
        answer = (
            "从武汉到桂林，高铁直达大约 5 小时，二等座 280 元左右；"
            "飞机 CZ3456 全程 1.5 小时，价格区间 600-900 元；自驾约 8 小时。"
        )

        request, append_chunk = await self._run(answer=answer, content_blocks=[])

        self.assertEqual(append_chunk.await_args.args[2], answer)
        self.assertEqual(request.content_blocks[-1].text, answer)

    async def test_有工具证据时原答复原样保留(self):
        answer = "高铁直达大约 5 小时，二等座 280 元左右。"
        blocks = [
            SearchBlock(
                type="search",
                query="武汉到桂林",
                sources=[SearchSourceSummary(title="来源", url="https://example.com/a")],
                source_refs=[SourceReference(kind="search", url="https://example.com/a")],
            )
        ]

        request, append_chunk = await self._run(answer=answer, content_blocks=blocks)

        self.assertEqual(append_chunk.await_args.args[2], answer)
        self.assertEqual(request.content_blocks[-1].text, answer)

    async def test_零工具证据但诚实答复不被改写(self):
        honest = "本次没能查到具体的班次和票价，建议稍后重试。"

        request, append_chunk = await self._run(answer=honest, content_blocks=[])

        self.assertEqual(append_chunk.await_args.args[2], honest)
        self.assertEqual(request.content_blocks[-1].text, honest)

    async def test_零工具证据时提示词要求证据不足就直说(self):
        request, prepare_context_fn = self._standard_request(
            answer="本次没能查到。",
            content_blocks=[],
            user_message="国庆想带父母从武汉去桂林玩，怎么过去省心一些？",
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            await run_limit_summary_step(request=request)

        summary_prompt = request.messages[-1]["content"]
        self.assertIn("No tool result was obtained", summary_prompt)
        self.assertIn("Do not provide dynamic data", summary_prompt)

    async def test_有工具证据时不追加无证据提示词(self):
        blocks = [
            SearchBlock(
                type="search",
                query="武汉到桂林",
                sources=[SearchSourceSummary(title="来源", url="https://example.com/a")],
                source_refs=[SourceReference(kind="search", url="https://example.com/a")],
            )
        ]
        request, prepare_context_fn = self._standard_request(
            answer="根据搜索结果，建议高铁。",
            content_blocks=blocks,
            user_message="国庆想带父母从武汉去桂林玩，怎么过去省心一些？",
        )
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare_context_fn),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()),
        ):
            await run_limit_summary_step(request=request)

        summary_prompt = request.messages[-1]["content"]
        self.assertNotIn("No tool result was obtained", summary_prompt)


class McpLimitSummaryTests(unittest.IsolatedAsyncioTestCase):
    """MCP 工具失败或成功后触顶，模型的总结都原样交付，服务端不替换回答。"""

    _ANSWER = "票价 300 元，08:15 发车。"

    @staticmethod
    def _resolution() -> RunCapabilityResolution:
        return RunCapabilityResolution(
            schema_version=3,
            router_version="test",
            package_id="agent",
            reason_codes=("all_available_tools",),
            external_tool_names=("mcp_fare_lookup",),
            effective_plan_mode="off",
            network_boundary_required=False,
        )

    async def _run(self, mcp_result: ToolResult):
        evidence = RecoveryEvidenceWorkset()
        evidence.record_result("mcp_fare_lookup", mcp_result)
        request, prepare = LimitSummaryNoEvidenceFactBoundaryTests()._standard_request(
            answer=self._ANSWER,
            content_blocks=[],
            user_message="用 mcp_fare_lookup 查一下票价",
        )
        request = replace(request, capability_resolution=self._resolution(), recovery_evidence=evidence)
        with (
            patch("app.services.stream.limit_summary.prepare_context", new=prepare),
            patch("app.services.stream.limit_summary.append_chunk", new=AsyncMock()) as append,
        ):
            outcome = await run_limit_summary_step(request=request)
        return request, append, outcome

    async def test_MCP失败后触顶原样交付模型总结(self):
        request, append, outcome = await self._run(
            ToolResult(status="failed", data={"error_code": "server_unavailable"})
        )

        self.assertEqual(append.await_args.args[2], self._ANSWER)
        self.assertEqual(request.content_blocks[-1].text, self._ANSWER)
        self.assertFalse(outcome.incomplete)

    async def test_MCP成功后触顶原样交付(self):
        request, append, outcome = await self._run(ToolResult(status="success", data={"payload": {"fare_yuan": 300}}))

        self.assertEqual(append.await_args.args[2], self._ANSWER)
        self.assertEqual(request.content_blocks[-1].text, self._ANSWER)
        self.assertFalse(outcome.incomplete)
