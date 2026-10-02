import json
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.documents.draft_stream import DRAFT_FIELDS, DocumentDraftStreamer, TopLevelStringFieldScanner
from app.services.stream import llm_stream as llm_stream_module
from app.services.stream.agent_round import _document_draft_observer
from app.services.stream.sse_encoder import entry_to_sse_envelope

DOCUMENT_TOOLS = frozenset({"create_document", "edit_document"})


def _feed_in_random_pieces(text: str, rng: random.Random) -> dict[str, str]:
    scanner = TopLevelStringFieldScanner(DRAFT_FIELDS)
    collected: dict[str, str] = {}
    index = 0
    while index < len(text):
        size = rng.randint(1, 7)
        for key, delta in scanner.feed(text[index : index + size]):
            collected[key] = collected.get(key, "") + delta
        index += size
    return collected


class TopLevelStringFieldScannerTests(unittest.TestCase):
    def test_matches_json_decoding_for_arbitrary_splits(self):
        rng = random.Random(7)
        alphabet = 'ab 中文\n"\\/\t😀é{}[],:'

        def random_text() -> str:
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))

        for _ in range(500):
            payload = {
                "edits": [{"old_text": random_text(), "new_text": random_text()}],
                "document_id": random_text(),
                "flag": rng.choice([1, True, None, 2.5]),
                "title": random_text(),
                "content": random_text(),
            }
            keys = list(payload)
            rng.shuffle(keys)
            ordered = {key: payload[key] for key in keys}
            text = json.dumps(ordered, ensure_ascii=rng.random() < 0.5, indent=rng.choice([None, 2]))

            collected = _feed_in_random_pieces(text, rng)

            self.assertEqual(collected.get("title", ""), payload["title"], text)
            self.assertEqual(collected.get("content", ""), payload["content"], text)
            self.assertEqual(collected.get("document_id", ""), payload["document_id"], text)
            self.assertEqual(set(collected) - DRAFT_FIELDS, set())

    def test_streams_unterminated_content_progressively(self):
        scanner = TopLevelStringFieldScanner(DRAFT_FIELDS)

        self.assertEqual(scanner.feed('{"title":"香港'), [("title", "香港")])
        self.assertEqual(scanner.feed('攻略","content":"# 第一天\\n'), [("title", "攻略"), ("content", "# 第一天\n")])
        self.assertEqual(scanner.feed("\\ud83d"), [])
        self.assertEqual(scanner.feed("\\ude00 出发"), [("content", "😀 出发")])


class DocumentDraftStreamerTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_started_then_field_deltas_for_document_tools_only(self):
        events = []

        async def emit(payload):
            events.append(payload)

        streamer = DocumentDraftStreamer(tool_names=DOCUMENT_TOOLS, draft_id_prefix="step-1", emit=emit, flush_chars=1)

        await streamer.on_tool_call_delta(0, "weather_forecast", '{"location":"香港"}')
        await streamer.on_tool_call_delta(1, None, '{"title":"香')
        await streamer.on_tool_call_delta(1, "create_document", '港","content":"正文')
        await streamer.on_tool_call_delta(1, None, '继续"}')

        self.assertEqual(
            events,
            [
                {"draft_id": "step-1:1", "tool_name": "create_document", "phase": "started"},
                {"draft_id": "step-1:1", "tool_name": "create_document", "field": "title", "delta": "香港"},
                {"draft_id": "step-1:1", "tool_name": "create_document", "field": "content", "delta": "正文"},
                {"draft_id": "step-1:1", "tool_name": "create_document", "field": "content", "delta": "继续"},
            ],
        )

    async def test_edit_document_streams_target_id_without_edit_bodies(self):
        events = []

        async def emit(payload):
            events.append(payload)

        streamer = DocumentDraftStreamer(tool_names=DOCUMENT_TOOLS, draft_id_prefix="step-2", emit=emit)
        await streamer.on_tool_call_delta(
            0,
            "edit_document",
            '{"document_id":"doc-1","edits":[{"old_text":"旧","new_text":"新"}]}',
        )

        self.assertEqual(
            events,
            [
                {"draft_id": "step-2:0", "tool_name": "edit_document", "phase": "started"},
                {"draft_id": "step-2:0", "tool_name": "edit_document", "field": "document_id", "delta": "doc-1"},
            ],
        )


class DocumentDraftCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_small_pieces_are_merged_until_size_or_interval_threshold(self):
        events = []
        now = [0.0]

        async def emit(payload):
            events.append(payload)

        streamer = DocumentDraftStreamer(
            tool_names=DOCUMENT_TOOLS,
            draft_id_prefix="step",
            emit=emit,
            clock=lambda: now[0],
            flush_chars=10,
            flush_interval_s=0.25,
        )
        await streamer.on_tool_call_delta(0, "create_document", '{"title":"攻略","content":"一二')
        await streamer.on_tool_call_delta(0, None, "三四")
        now[0] = 0.3
        await streamer.on_tool_call_delta(0, None, "五")
        await streamer.on_tool_call_delta(0, None, "六七八九十甲乙丙丁戊")

        self.assertEqual(
            [(event.get("field"), event.get("delta")) for event in events],
            [(None, None), ("title", "攻略"), ("content", "一二三四五"), ("content", "六七八九十甲乙丙丁戊")],
        )


class StreamRoundToolCallObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_sees_argument_pieces_and_accumulation_is_unchanged(self):
        seen = []

        async def observer(index, name, arguments):
            seen.append((index, name, arguments))

        def tool_delta(name, arguments):
            return SimpleNamespace(
                tool_calls=[
                    SimpleNamespace(index=0, id="call-1", function=SimpleNamespace(name=name, arguments=arguments))
                ],
                content=None,
            )

        async def response():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=tool_delta("create_document", '{"title":'), finish_reason=None)],
                usage=None,
            )
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=tool_delta(None, '"文档"}'), finish_reason="tool_calls")],
                usage=None,
            )

        with (
            patch("app.services.stream.llm_stream.append_chunk", new=AsyncMock()),
            patch("app.services.stream.llm_stream.check_lock_owner", new=AsyncMock(return_value=True)),
        ):
            result = await llm_stream_module.stream_round(
                response(),
                "conv",
                "task",
                False,
                "thinking",
                "text",
                on_tool_call_delta=observer,
            )

        self.assertEqual(seen, [(0, "create_document", '{"title":'), (0, None, '"文档"}')])
        self.assertEqual(result[2], [{"id": "call-1", "name": "create_document", "arguments": '{"title":"文档"}'}])


class DocumentDraftObserverTests(unittest.IsolatedAsyncioTestCase):
    def test_no_observer_when_round_has_no_document_tools(self):
        observer = _document_draft_observer(
            frozenset(),
            conversation_id="conv",
            task_id="task",
            run_id="run",
            step_id="step",
        )

        self.assertIsNone(observer)

    async def test_observer_writes_draft_chunks_to_the_run_stream(self):
        observer = _document_draft_observer(
            DOCUMENT_TOOLS,
            conversation_id="conv",
            task_id="task",
            run_id="run",
            step_id="step",
        )
        append = AsyncMock(return_value="1-0")
        with patch("app.services.stream.agent_round.append_chunk", new=append):
            await observer(0, "create_document", '{"content":"' + "第一段" * 70)

        self.assertEqual(append.await_count, 2)
        _, chunk_type, content, block_id = append.await_args_list[1].args
        self.assertEqual(chunk_type, "document_draft")
        self.assertEqual(block_id, "step:0")
        self.assertEqual(json.loads(content)["delta"], "第一段" * 70)
        self.assertEqual(append.await_args_list[1].kwargs, {"task_id": "task", "run_id": "run", "step_id": "step"})


class DocumentDraftSseTests(unittest.TestCase):
    def test_document_draft_entry_becomes_envelope_with_run_identity(self):
        envelope = entry_to_sse_envelope(
            {
                "type": "document_draft",
                "content": json.dumps(
                    {"draft_id": "step:0", "tool_name": "create_document", "field": "content", "delta": "正文"}
                ),
                "block_id": "step:0",
                "run_id": "run",
                "step_id": "step",
            }
        )

        self.assertEqual(envelope["chunk_type"], "document_draft")
        self.assertEqual(
            envelope["data"],
            {
                "draft_id": "step:0",
                "tool_name": "create_document",
                "field": "content",
                "delta": "正文",
                "run_id": "run",
                "step_id": "step",
            },
        )

    def test_malformed_draft_entry_degrades_to_empty_payload(self):
        envelope = entry_to_sse_envelope({"type": "document_draft", "content": "{broken", "block_id": "x"})

        self.assertEqual(envelope, {"chunk_type": "document_draft", "data": {}})


class DriverDraftToolNamesTests(unittest.IsolatedAsyncioTestCase):
    async def _captured_round_kwargs(self, output_tool_names):
        from app.schemas.chat import Usage
        from app.services.stream.agent_loop_driver import _run_round
        from app.services.stream.agent_loop_state import AgentLoopState
        from app.services.stream.agent_round import AgentRoundResult
        from app.services.stream.step_lifecycle import AgentStepContext
        from test.services.stream.test_agent_loop_driver import _runtime

        captured = []

        async def run_round_fn(**kwargs):
            captured.append(kwargs)
            return AgentRoundResult(
                reasoning_buf="",
                content_buf="",
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=1, output_tokens=1),
            )

        await _run_round(
            messages=[{"role": "user", "content": "出一份攻略"}],
            state=AgentLoopState(),
            runtime=_runtime(run_round_fn=run_round_fn, output_tool_names=output_tool_names),
            step_number=1,
            step_context=AgentStepContext(
                step_id="step-doc",
                step_number=1,
                started_at=1.0,
                thinking_block_id="thinking-doc",
                text_block_id="text-doc",
            ),
        )
        return captured[0]

    async def test_document_runs_pass_output_tools_for_draft_preview(self):
        kwargs = await self._captured_round_kwargs(DOCUMENT_TOOLS)

        self.assertEqual(kwargs["draft_tool_names"], DOCUMENT_TOOLS)

    async def test_chat_runs_do_not_request_draft_preview(self):
        kwargs = await self._captured_round_kwargs(frozenset())

        self.assertNotIn("draft_tool_names", kwargs)
