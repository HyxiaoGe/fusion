import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.prompts.prompt_message import PromptMessage
from app.db.database import Base
from app.db.models import Conversation as ConversationModel
from app.db.models import Message as MessageModel
from app.db.models import User as UserModel
from app.schemas.chat import Message, TextBlock
from app.services.chat.context_manager import prepare_context
from app.services.chat.message_builder import build_llm_messages
from app.services.chat.tool_transcript import (
    advance_cutoff,
    max_citation_index,
    replay_messages,
    transcript_entries,
    without_tool_transactions,
)
from app.services.chat.tool_transcript_store import load_tool_transcripts, save_tool_transcript
from app.services.stream.agent_loop_state import AgentLoopState


def _call(call_id: str, name: str = "web_search") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": '{"query": "q"}'}}


def _transcript(*call_ids: str) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [_call(call_id) for call_id in call_ids]},
        *({"role": "tool", "tool_call_id": call_id, "content": f"result {call_id}"} for call_id in call_ids),
    ]


def _tool_result(call_id: str) -> PromptMessage:
    return PromptMessage(role="tool", content="x", provider_fields={"tool_call_id": call_id})


def _length_estimator(_model, messages, _tools):
    return sum(len(str(message.get("content") or "")) for message in messages)


class ToolTranscriptTests(unittest.TestCase):
    def test_entries_drop_reasoning_and_inline_think(self):
        entries = transcript_entries(
            [
                PromptMessage(
                    role="assistant",
                    content="<think>先搜一下</think>查一下",
                    provider_fields={"tool_calls": [_call("c1")], "reasoning_content": "长思考"},
                ),
                PromptMessage(role="tool", content="结果", provider_fields={"tool_call_id": "c1"}),
            ]
        )

        self.assertEqual(
            entries,
            [
                {"role": "assistant", "content": "查一下", "tool_calls": [_call("c1")]},
                {"role": "tool", "tool_call_id": "c1", "content": "结果"},
            ],
        )

    def test_replay_restores_protocol_messages_without_reasoning(self):
        replayed = replay_messages(_transcript("c1", "c2"))

        self.assertEqual([message.role for message in replayed], ["assistant", "tool", "tool"])
        self.assertEqual(replayed[0].provider_fields, {"tool_calls": [_call("c1"), _call("c2")]})
        self.assertEqual(replayed[2].provider_fields, {"tool_call_id": "c2"})

    def test_replay_skips_transactions_with_missing_results(self):
        transcript = [
            {"role": "assistant", "content": "", "tool_calls": [_call("c1"), _call("c2")]},
            {"role": "tool", "tool_call_id": "c1", "content": "only one"},
            *_transcript("c3"),
        ]

        replayed = replay_messages(transcript)

        self.assertEqual([message.get("tool_call_id") for message in replayed if message.role == "tool"], ["c3"])
        self.assertEqual(replay_messages("broken"), [])

    def test_cutoff_advances_to_latest_turn_removed_by_the_trim_only(self):
        history = {"c1": 2, "c2": 4, "c3": 6}
        all_results = [_tool_result(call_id) for call_id in history]

        def advance(current, before, after):
            return advance_cutoff(current, history_sequences=history, before_messages=before, after_messages=after)

        self.assertEqual(advance(None, all_results, [_tool_result("c3")]), 4)
        self.assertEqual(advance(8, all_results, [_tool_result("c3")]), 8)
        self.assertIsNone(advance(None, all_results, all_results))
        # 已在本次调用前移除的历史事务（无工具定义的请求）不算上下文管理删掉的。
        self.assertIsNone(advance(None, [_tool_result("current")], []))

    def test_state_records_cutoff_from_trimmed_context(self):
        state = AgentLoopState(history_tool_call_sequences={"c1": 2})

        state.record_context_plan([_tool_result("c1")], [PromptMessage(role="user", content="q")])

        self.assertEqual(state.tool_transcript_cutoff_sequence, 2)

    def test_without_tool_transactions_removes_only_listed_history(self):
        messages = [
            PromptMessage(role="user", content="q1"),
            *replay_messages(_transcript("old")),
            PromptMessage(role="assistant", content="a1"),
            PromptMessage(role="user", content="q2"),
            *replay_messages(_transcript("new")),
        ]

        kept = without_tool_transactions(messages, {"old"})

        self.assertEqual(
            [(message.role, message.get("tool_call_id")) for message in kept],
            [("user", None), ("assistant", None), ("user", None), ("assistant", None), ("tool", "new")],
        )
        self.assertIsNotNone(kept[3].get("tool_calls"))

    def test_max_citation_index_reads_search_and_url_read_sources(self):
        blocks = [
            {"type": "text", "text": "[9]"},
            {"type": "search", "sources": [{"citation_index": 3}], "source_refs": [{"citation_index": 5}]},
            SimpleNamespace(type="url_read", source_refs=[SimpleNamespace(citation_index=7)], sources=None),
        ]

        self.assertEqual(max_citation_index(blocks), 7)


class MessageBuilderReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_transcript_is_replayed_before_its_answer(self):
        history = [
            Message(id="u1", role="user", content=[TextBlock(type="text", text="查天气")]),
            Message(id="a1", role="assistant", content=[TextBlock(type="text", text="晴天")]),
            Message(id="u2", role="user", content=[TextBlock(type="text", text="明天呢")]),
        ]

        result = await build_llm_messages(
            history,
            has_vision=False,
            file_repo=None,
            user_system_prompt=None,
            include_base_system=False,
            tool_transcripts={"a1": _transcript("c1")},
        )

        self.assertEqual(
            [(message.role, message.content) for message in result],
            [("user", "查天气"), ("assistant", ""), ("tool", "result c1"), ("assistant", "晴天"), ("user", "明天呢")],
        )


class ContextTrimOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_tool_results_go_before_old_question_and_answer_text(self):
        canonical = [
            {"role": "system", "content": "s" * 5},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [_call("old")]},
            {"role": "tool", "tool_call_id": "old", "content": "r" * 80},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "", "tool_calls": [_call("new")]},
            {"role": "tool", "tool_call_id": "new", "content": "n" * 40},
        ]

        plan = await prepare_context(
            messages=canonical,
            model_id="model-a",
            litellm_model="litellm_proxy/model-a",
            call_kwargs={},
            window_resolver=lambda _model: (100, "test", "known"),
            token_estimator=_length_estimator,
            run_in_thread=False,
            use_fast_path=False,
        )

        self.assertEqual(plan.status, "trimmed")
        self.assertEqual([message.get("content") for message in plan.messages[:3]], ["s" * 5, "q1", "a1"])
        self.assertEqual([message.get("tool_call_id") for message in plan.messages if message.role == "tool"], ["new"])


class ToolTranscriptStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(UserModel(id="user-1", username="user-1"))
        self.db.add(ConversationModel(id="conv-1", user_id="user-1", title="t", model_id="m"))
        for sequence, message_id in ((2, "a1"), (4, "a2")):
            self.db.add(
                MessageModel(
                    id=message_id,
                    conversation_id="conv-1",
                    role="assistant",
                    content=[{"type": "text", "id": message_id, "text": "答"}],
                    sequence=sequence,
                )
            )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_saved_transcripts_after_cutoff_are_loaded(self):
        self.assertTrue(
            save_tool_transcript(
                self.db, conversation_id="conv-1", message_id="a1", transcript=_transcript("c1"), cutoff_sequence=None
            )
        )
        save_tool_transcript(
            self.db, conversation_id="conv-1", message_id="a2", transcript=_transcript("c2"), cutoff_sequence=None
        )

        loaded = load_tool_transcripts(self.db, "conv-1", ["a1", "a2"])
        self.assertEqual(set(loaded.transcripts), {"a1", "a2"})
        self.assertEqual(loaded.sequences, {"c1": 2, "c2": 4})

        save_tool_transcript(self.db, conversation_id="conv-1", message_id="a2", transcript=[], cutoff_sequence=2)
        loaded = load_tool_transcripts(self.db, "conv-1", ["a1", "a2"])
        self.assertEqual(loaded.cutoff_sequence, 2)
        self.assertEqual(set(loaded.transcripts), {"a2"})

    def test_cutoff_never_moves_backward(self):
        save_tool_transcript(self.db, conversation_id="conv-1", message_id="a2", transcript=[], cutoff_sequence=4)
        save_tool_transcript(self.db, conversation_id="conv-1", message_id="a2", transcript=[], cutoff_sequence=2)

        self.assertEqual(load_tool_transcripts(self.db, "conv-1", []).cutoff_sequence, 4)


if __name__ == "__main__":
    unittest.main()
