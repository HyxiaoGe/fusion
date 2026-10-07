"""运行收尾必须与通知原子提交，迟到失败不能改写已完成结果。"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import AgentSession, Conversation, Notification, User
from app.services.agent.session_cache import write_session_status


class SessionNotificationCommitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add(User(id="u", username="消息用户", email="notifications@example.com"))
            db.add(Conversation(id="c", user_id="u", title="厦门攻略", model_id="m"))
            db.add(
                AgentSession(
                    id="r",
                    conversation_id="c",
                    message_id="answer",
                    user_id="u",
                    model_id="m",
                    provider="p",
                    status="running",
                )
            )
            db.commit()
        self.session_patch = patch("app.services.agent.session_cache.SessionLocal", self.factory)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.addCleanup(self.engine.dispose)

    async def test_committed_completion_is_not_replaced_by_late_error(self):
        for status in ["completed", "error", "completed"]:
            await write_session_status(run_id="r", status=status, total_steps=1, total_tool_calls=0)
        with self.factory() as db:
            self.assertEqual(db.get(AgentSession, "r").status, "completed")
            rows = db.scalars(select(Notification)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].kind, "run_completed")

    async def test_notification_failure_rolls_back_terminal(self):
        with patch(
            "app.services.notification_service.enqueue_run_notification", side_effect=RuntimeError("通知保存失败")
        ):
            with self.assertRaises(RuntimeError):
                await write_session_status(run_id="r", status="completed", total_steps=1, total_tool_calls=0)
        with self.factory() as db:
            self.assertEqual(db.get(AgentSession, "r").status, "running")
            self.assertEqual(db.scalars(select(Notification)).all(), [])
