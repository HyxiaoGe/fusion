import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import AgentEvent, AgentProgressSnapshot, AgentSession, ConversationFile, File, ToolCallLog
from app.db.models import Conversation as ConversationModel
from app.db.models import Message as MessageModel
from app.db.models import User as UserModel
from app.db.repositories import ConversationRepository, FileRepository


class MessageRepositoryTests(unittest.TestCase):
    def test_convert_message_isolates_unknown_future_and_corrupt_blocks(self):
        db_message = MessageModel(
            id="msg-mixed",
            conversation_id="conv-1",
            role="assistant",
            content=[
                {"type": "text", "id": "answer-1", "text": "有效回答"},
                {"type": "future_widget", "id": "future-1", "payload": {}},
                {
                    "type": "place_results",
                    "id": "future-place",
                    "schema_version": 2,
                    "provider": "future-provider",
                },
                {"type": "route_results", "id": "broken-route", "schema_version": 1},
                {"type": "text", "id": "answer-2", "text": "仍能恢复"},
            ],
            created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)

        self.assertEqual(
            [block.type for block in message.content],
            ["text", "unsupported_result", "unsupported_result", "unsupported_result", "text"],
        )
        self.assertEqual(message.content[1].reason, "unsupported_type")
        self.assertEqual(message.content[2].reason, "unsupported_version")
        self.assertEqual(message.content[3].reason, "invalid_payload")

    def test_convert_message_keeps_future_only_message_as_safe_fallback(self):
        db_message = MessageModel(
            id="msg-future-only",
            conversation_id="conv-1",
            role="assistant",
            content=[
                {
                    "type": "place_results",
                    "id": "future-place",
                    "schema_version": 2,
                    "provider": "PRIVATE_PROVIDER",
                    "raw_payload": {"secret": "PRIVATE_SECRET"},
                }
            ],
            created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)

        self.assertEqual(len(message.content), 1)
        fallback = message.content[0]
        self.assertEqual(fallback.type, "unsupported_result")
        self.assertEqual(fallback.id, "future-place")
        self.assertEqual(fallback.source_type, "place_results")
        self.assertEqual(fallback.source_schema_version, 2)
        self.assertEqual(fallback.reason, "unsupported_version")
        self.assertNotIn("PRIVATE", str(fallback.model_dump(mode="json")))

    def test_convert_message_keeps_reasoning_text_without_phrase_filtering(self):
        db_message = MessageModel(
            id="msg-thinking-control",
            conversation_id="conv-1",
            role="assistant",
            content=[
                {
                    "type": "thinking",
                    "id": "thinking-control",
                    "thinking": (
                        "先比较方案风险。\n\n"
                        "According to the autonomous web search rules: do not search. "
                        "本轮启用了强制计划模式。"
                    ),
                }
            ],
            created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)

        self.assertEqual(message.content[0].thinking, db_message.content[0]["thinking"])

    def test_convert_message_restores_nested_context_and_accepts_legacy_usage(self):
        current = MessageModel(
            id="msg-current",
            conversation_id="conv-1",
            role="assistant",
            content=[{"type": "text", "id": "answer-1", "text": "回答"}],
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "context": {
                    "status": "trimmed",
                    "window_tokens": 1000,
                    "estimated_tokens_before": 900,
                    "estimated_tokens_after": 700,
                    "actual_prompt_tokens": 690,
                    "removed_turns": 1,
                    "removed_messages": 2,
                    "removed_tool_transactions": 0,
                },
            },
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        legacy = MessageModel(
            id="msg-legacy",
            conversation_id="conv-1",
            role="assistant",
            content=[{"type": "text", "id": "answer-2", "text": "旧回答"}],
            usage={"input_tokens": 12, "output_tokens": 8},
            created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
        repo = ConversationRepository(None)

        current_message = repo._convert_message_to_schema(current)
        legacy_message = repo._convert_message_to_schema(legacy)

        self.assertEqual(current_message.usage.context.actual_prompt_tokens, 690)
        self.assertEqual(current_message.usage.input_tokens, 100)
        self.assertIsNone(legacy_message.usage.context)

    def test_convert_message_discards_corrupt_context_but_keeps_tokens(self):
        db_message = MessageModel(
            id="msg-corrupt",
            conversation_id="conv-1",
            role="assistant",
            content=[{"type": "text", "id": "answer-1", "text": "回答"}],
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "context": {"status": "unknown-future", "window_tokens": -1},
            },
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)

        self.assertEqual(message.usage.input_tokens, 100)
        self.assertEqual(message.usage.output_tokens, 20)
        self.assertIsNone(message.usage.context)

    def test_convert_message_preserves_search_provider_metadata(self):
        """从 JSONB 重建 SearchBlock 时保留搜索提供方元信息"""
        db_message = MessageModel(
            id="msg-1",
            conversation_id="conv-1",
            role="assistant",
            created_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            content=[
                {
                    "type": "search",
                    "id": "search-1",
                    "query": "AI 标准",
                    "tool_call_log_id": "log-1",
                    "sources": [{"title": "来源", "url": "https://example.com"}],
                    "requested_provider": "firecrawl",
                    "result_provider": "brave",
                    "fallback_used": True,
                    "provider_chain": ["firecrawl", "brave"],
                }
            ],
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)

        search_block = message.content[0]
        self.assertEqual(search_block.requested_provider, "firecrawl")
        self.assertEqual(search_block.result_provider, "brave")
        self.assertTrue(search_block.fallback_used)
        self.assertEqual(search_block.provider_chain, ["firecrawl", "brave"])

    def test_delete_file_requires_owner_and_removes_conversation_link(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            user = UserModel(id="user-1", username="user-1")
            other_user = UserModel(id="user-2", username="user-2")
            conversation = ConversationModel(
                id="conv-1",
                user_id="user-1",
                title="会话",
                model_id="qwen",
                created_at=datetime(2026, 7, 4, 10, 0, 0),
                updated_at=datetime(2026, 7, 4, 10, 0, 0),
            )
            file = File(
                id="file-1",
                user_id="user-1",
                filename="file-1_photo.png",
                original_filename="photo.png",
                mimetype="image/png",
                size=12,
                path="conv-1/file-1/processed.jpg",
                status="processed",
            )
            link = ConversationFile(conversation_id="conv-1", file_id="file-1")
            db.add_all([user, other_user, conversation, file, link])
            db.commit()

            repo = FileRepository(db)

            self.assertFalse(repo.delete_file("file-1", "user-2"))
            self.assertIsNotNone(db.query(File).filter(File.id == "file-1").first())
            self.assertIsNotNone(db.query(ConversationFile).filter(ConversationFile.file_id == "file-1").first())

            self.assertTrue(repo.delete_file("file-1", "user-1"))
            self.assertIsNone(db.query(File).filter(File.id == "file-1").first())
            self.assertIsNone(db.query(ConversationFile).filter(ConversationFile.file_id == "file-1").first())
        finally:
            db.close()
            engine.dispose()

    def test_get_by_id_attaches_latest_agent_run_summary_to_assistant_message(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = ConversationModel(
                id="conv-1",
                user_id="user-1",
                title="会话",
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 0),
                updated_at=datetime(2026, 6, 28, 10, 0, 0),
            )
            message = MessageModel(
                id="msg-1",
                conversation_id="conv-1",
                role="assistant",
                content=[{"type": "text", "id": "blk_1", "text": "旧回答"}],
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 1),
            )
            run = AgentSession(
                id="run-1",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="deepseek-chat",
                provider="deepseek",
                run_config={
                    "max_steps": 3,
                    "max_tool_calls": 5,
                    "timeout_s": 60,
                    "system_prompt_snapshot": {"sections": [{"content": "不能进入会话摘要的提示词"}]},
                },
                total_steps=3,
                total_tool_calls=5,
                status="limit_reached",
                limit_reason="max_steps",
                created_at=datetime(2026, 6, 28, 10, 1, 0),
            )
            db.add_all([conversation, message, run])
            db.commit()

            result = ConversationRepository(db).get_by_id("conv-1", "user-1")

            self.assertIsNotNone(result)
            self.assertEqual(result.messages[0].agent_run.run_id, "run-1")
            self.assertEqual(result.messages[0].agent_run.status, "limit_reached")
            self.assertEqual(result.messages[0].agent_run.limit_reason, "max_steps")
            self.assertEqual(result.messages[0].agent_run.config["max_steps"], 3)
            self.assertNotIn("system_prompt_snapshot", result.messages[0].agent_run.config)
            self.assertNotIn("不能进入会话摘要", result.model_dump_json())
        finally:
            db.close()
            engine.dispose()

    def test_get_by_id_restores_tool_calls_per_step_from_tool_call_logs(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = ConversationModel(id="conv-1", user_id="user-1", title="会话", model_id="deepseek-chat")
            message = MessageModel(
                id="msg-1",
                conversation_id="conv-1",
                role="assistant",
                content=[{"type": "text", "id": "blk_1", "text": "回答"}],
                model_id="deepseek-chat",
            )
            run = AgentSession(
                id="run-1",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="deepseek-chat",
                provider="deepseek",
                status="completed",
            )

            def log(log_id, *, trace_id="run-1", step_number, tool_call_id, minute, **fields):
                return ToolCallLog(
                    id=log_id,
                    conversation_id="conv-1",
                    user_id="user-1",
                    model_id="deepseek-chat",
                    provider="deepseek",
                    trace_id=trace_id,
                    step_number=step_number,
                    tool_call_id=tool_call_id,
                    created_at=datetime(2026, 10, 22, 10, minute, 0),
                    **{"tool_name": "web_search", "status": "success", **fields},
                )

            db.add_all(
                [
                    conversation,
                    message,
                    run,
                    log(
                        "log-3",
                        step_number=2,
                        tool_call_id="call-3",
                        minute=3,
                        tool_name="url_read",
                        status="failed",
                        error_message="超时" * 200,
                        input_params={"url": "https://a.example"},
                    ),
                    log(
                        "log-2",
                        step_number=1,
                        tool_call_id="call-2",
                        minute=2,
                        input_params={"query": "故宫门票"},
                        output_data={"results": ["工具输出不进历史摘要"]},
                        duration_ms=1200,
                    ),
                    log("log-1", step_number=1, tool_call_id="call-1", minute=1, input_params={"query": "北京天气"}),
                    log(
                        "log-place",
                        step_number=2,
                        tool_call_id="call-4",
                        minute=4,
                        tool_name="weather_forecast",
                        status="failed",
                        error_message="天气查询失败",
                        output_data={"status": "failed", "error_code": "ambiguous_location"},
                    ),
                    log("log-other", trace_id="run-other", step_number=1, tool_call_id="call-x", minute=1),
                    log("log-unlinked", step_number=None, tool_call_id="call-y", minute=4),
                ]
            )
            db.commit()

            result = ConversationRepository(db).get_by_id("conv-1", "user-1")

            steps = result.messages[0].agent_run.steps
            self.assertEqual([step.step_number for step in steps], [1, 2])
            self.assertEqual([call.tool_call_id for call in steps[0].tool_calls], ["call-1", "call-2"])
            self.assertEqual(steps[0].tool_calls[1].arguments, {"query": "故宫门票"})
            self.assertEqual(steps[0].tool_calls[1].duration_ms, 1200)
            failed = steps[1].tool_calls[0]
            self.assertEqual((failed.tool_name, failed.status), ("url_read", "failed"))
            self.assertEqual(len(failed.error), 200)
            self.assertIsNone(failed.repair_state)
            # 地名多候选与实时事件同口径：待确认而不是失败。
            place = steps[1].tool_calls[1]
            self.assertEqual((place.status, place.repair_state, place.error), ("degraded", "awaiting_choice", None))
            self.assertNotIn("工具输出不进历史摘要", result.model_dump_json())
        finally:
            db.close()
            engine.dispose()

    def test_get_by_id_restores_model_fallbacks_with_step_numbers(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = ConversationModel(id="conv-1", user_id="user-1", title="会话", model_id="mimo-v2.6-flash")
            message = MessageModel(
                id="msg-1",
                conversation_id="conv-1",
                role="assistant",
                content=[{"type": "text", "id": "blk_1", "text": "回答"}],
                model_id="mimo-v2.6-flash",
            )
            run = AgentSession(
                id="run-1",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="mimo-v2.6-flash",
                provider="openai",
                status="completed",
            )

            def event(sequence, event_type, step_id, payload, run_id="run-1"):
                return AgentEvent(
                    conversation_id="conv-1",
                    message_id="msg-1",
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    step_id=step_id,
                    event_ts=datetime(2026, 10, 22, 10, 0, sequence, tzinfo=timezone.utc),
                    payload={"type": event_type, **payload},
                )

            other_run = AgentSession(
                id="run-other",
                conversation_id="conv-1",
                message_id="msg-other",
                user_id="user-1",
                model_id="mimo-v2.6-flash",
                provider="openai",
                status="completed",
            )
            db.add_all(
                [
                    conversation,
                    message,
                    run,
                    other_run,
                    event(1, "step_started", "step-a", {"step_number": 1}),
                    event(
                        2,
                        "model_fallback",
                        "step-a",
                        {
                            "failed_model": "mimo-v2.6-flash",
                            "fallback_model": "deepseek-chat",
                            "reason": "first_output_timeout",
                        },
                    ),
                    event(3, "step_started", "step-b", {"step_number": 2}),
                    event(
                        4,
                        "model_fallback",
                        "step-b",
                        {"failed_model": "deepseek-chat", "fallback_model": None, "reason": "circuit_open"},
                    ),
                    event(5, "model_fallback", None, {"failed_model": "deepseek-chat", "reason": "http_503"}),
                    event(6, "model_fallback", "step-b", {"reason": "缺少失败模型的坏记录"}),
                    event(
                        1, "model_fallback", None, {"failed_model": "x", "reason": "network_error"}, run_id="run-other"
                    ),
                ]
            )
            db.commit()

            result = ConversationRepository(db).get_by_id("conv-1", "user-1")

            fallbacks = result.messages[0].agent_run.model_fallbacks
            self.assertEqual(
                [(f.step_number, f.failed_model, f.fallback_model, f.reason) for f in fallbacks],
                [
                    (1, "mimo-v2.6-flash", "deepseek-chat", "first_output_timeout"),
                    (2, "deepseek-chat", None, "circuit_open"),
                    (None, "deepseek-chat", None, "http_503"),
                ],
            )
        finally:
            db.close()
            engine.dispose()

    def test_get_by_id_uses_latest_agent_run_not_latest_limit_reached(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = ConversationModel(
                id="conv-1",
                user_id="user-1",
                title="会话",
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 0),
                updated_at=datetime(2026, 6, 28, 10, 0, 0),
            )
            message = MessageModel(
                id="msg-1",
                conversation_id="conv-1",
                role="assistant",
                content=[{"type": "text", "id": "blk_1", "text": "旧回答"}],
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 1),
            )
            old_limit = AgentSession(
                id="run-old",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="deepseek-chat",
                provider="deepseek",
                total_steps=3,
                total_tool_calls=5,
                status="limit_reached",
                limit_reason="max_tool_calls",
                created_at=datetime(2026, 6, 28, 10, 1, 0),
            )
            latest_completed = AgentSession(
                id="run-new",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="deepseek-chat",
                provider="deepseek",
                total_steps=1,
                total_tool_calls=0,
                status="completed",
                limit_reason="max_steps",
                created_at=datetime(2026, 6, 28, 10, 2, 0),
            )
            db.add_all([conversation, message, old_limit, latest_completed])
            db.commit()

            result = ConversationRepository(db).get_by_id("conv-1", "user-1")

            self.assertIsNotNone(result)
            self.assertEqual(result.messages[0].agent_run.run_id, "run-new")
            self.assertEqual(result.messages[0].agent_run.status, "completed")
            self.assertIsNone(result.messages[0].agent_run.limit_reason)
        finally:
            db.close()
            engine.dispose()

    def test_get_by_id_attaches_latest_agent_progress_snapshot(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = ConversationModel(
                id="conv-1",
                user_id="user-1",
                title="会话",
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 0),
                updated_at=datetime(2026, 6, 28, 10, 0, 0),
            )
            message = MessageModel(
                id="msg-1",
                conversation_id="conv-1",
                role="assistant",
                content=[{"type": "text", "id": "blk_1", "text": "回答"}],
                model_id="deepseek-chat",
                created_at=datetime(2026, 6, 28, 10, 0, 1),
            )
            run = AgentSession(
                id="run-new",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                model_id="deepseek-chat",
                provider="deepseek",
                total_steps=1,
                total_tool_calls=1,
                status="completed",
                created_at=datetime(2026, 6, 28, 10, 2, 0),
            )
            snapshot = AgentProgressSnapshot(
                run_id="run-new",
                conversation_id="conv-1",
                message_id="msg-1",
                user_id="user-1",
                protocol_version=2,
                state={
                    "run_id": "run-new",
                    "message_id": "msg-1",
                    "status": "completed",
                    "progress": {"phase": "answering", "label": "正在整理回答"},
                    "plan": {"plan_id": "plan-run-new", "revision": 1, "items": []},
                    "tool_digests": [],
                    "evidence": [],
                },
            )
            db.add_all([conversation, message, run, snapshot])
            db.commit()

            result = ConversationRepository(db).get_by_id("conv-1", "user-1")

            self.assertIsNotNone(result)
            self.assertEqual(result.messages[0].agent_run.run_id, "run-new")
            self.assertEqual(result.messages[0].agent_run.progress["plan"]["plan_id"], "plan-run-new")
        finally:
            db.close()
            engine.dispose()
