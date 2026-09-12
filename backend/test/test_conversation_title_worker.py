"""会话标题并发生成与送达的契约。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Conversation, Message, User
from app.services.conversation_title_worker import run_conversation_title_worker


class ConversationTitleWorkerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        db.add(User(id="user-1", username="user-1"))
        db.add(
            Conversation(
                id="conv-1",
                user_id="user-1",
                title="第一个问题的截断标题",
                model_id="deepseek-chat",
            )
        )
        db.add(
            Message(
                id="user-msg-1",
                conversation_id="conv-1",
                role="user",
                sequence=1,
                content=[{"type": "text", "id": "u-1", "text": "第一个问题"}],
            )
        )
        db.commit()
        db.close()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)

    def _add_second_turn(self):
        db = self.Session()
        db.add(
            Message(
                id="user-msg-2",
                conversation_id="conv-1",
                role="user",
                sequence=3,
                content=[{"type": "text", "id": "u-2", "text": "第二个问题"}],
            )
        )
        db.commit()
        db.close()

    def test_首轮生成标题并推送(self):
        emit = AsyncMock()
        with patch(
            "app.services.chat_service.ChatService.generate_title",
            new=AsyncMock(return_value="Redis 缓存设计"),
        ):
            title = asyncio.run(
                run_conversation_title_worker(
                    conversation_id="conv-1",
                    user_id="user-1",
                    emit_fn=emit,
                    session_factory=self.Session,
                )
            )

        self.assertEqual(title, "Redis 缓存设计")
        emit.assert_awaited_once()
        kwargs = emit.await_args.kwargs
        self.assertEqual(kwargs["conversation_id"], "conv-1")
        self.assertEqual(kwargs["title"], "Redis 缓存设计")
        self.assertGreaterEqual(kwargs["duration_ms"], 0)

    def test_非首轮不生成也不推送(self):
        """标题取自首个提问；后续轮由用户手动重新生成。"""
        self._add_second_turn()
        emit = AsyncMock()
        generate = AsyncMock(return_value="不该被调用")
        with patch("app.services.chat_service.ChatService.generate_title", new=generate):
            title = asyncio.run(
                run_conversation_title_worker(
                    conversation_id="conv-1",
                    user_id="user-1",
                    emit_fn=emit,
                    session_factory=self.Session,
                )
            )

        self.assertIsNone(title)
        generate.assert_not_awaited()
        emit.assert_not_awaited()

    def test_生成失败只记录不抛出(self):
        emit = AsyncMock()
        with patch(
            "app.services.chat_service.ChatService.generate_title",
            new=AsyncMock(side_effect=RuntimeError("utility 模型不可用")),
        ):
            title = asyncio.run(
                run_conversation_title_worker(
                    conversation_id="conv-1",
                    user_id="user-1",
                    emit_fn=emit,
                    session_factory=self.Session,
                )
            )

        self.assertIsNone(title)
        emit.assert_not_awaited()

    def test_送达失败不影响已落库的标题(self):
        """封口后推送会被 emitter 拒绝；标题已落库，前端走会话列表刷新兜底。"""
        emit = AsyncMock(side_effect=RuntimeError("agent_event emitter 已封口"))
        with patch(
            "app.services.chat_service.ChatService.generate_title",
            new=AsyncMock(return_value="Redis 缓存设计"),
        ):
            title = asyncio.run(
                run_conversation_title_worker(
                    conversation_id="conv-1",
                    user_id="user-1",
                    emit_fn=emit,
                    session_factory=self.Session,
                )
            )

        self.assertEqual(title, "Redis 缓存设计")
        emit.assert_awaited_once()

    def test_没有送达通道时仍生成(self):
        with patch(
            "app.services.chat_service.ChatService.generate_title",
            new=AsyncMock(return_value="Redis 缓存设计"),
        ):
            title = asyncio.run(
                run_conversation_title_worker(
                    conversation_id="conv-1",
                    user_id="user-1",
                    emit_fn=None,
                    session_factory=self.Session,
                )
            )

        self.assertEqual(title, "Redis 缓存设计")


if __name__ == "__main__":
    unittest.main()
