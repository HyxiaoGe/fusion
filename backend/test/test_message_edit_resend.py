import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.responses import StreamingResponse

from app.api.chat import send_message
from app.db.database import Base
from app.db.models import Conversation as ConversationModel
from app.db.models import Message as MessageModel
from app.db.models import User as UserModel
from app.schemas.chat import ChatRequest, Conversation, FileBlock, Message, TextBlock
from app.schemas.response import ApiException
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService
from app.services.stream_state_service import StreamInitResult

USER_MESSAGE_ID = "11111111-1111-4111-8111-111111111111"
ASSISTANT_MESSAGE_ID = "22222222-2222-4222-8222-222222222222"


class EditResendRequestTests(unittest.TestCase):
    def test_edit_requires_target_user_message(self):
        with self.assertRaisesRegex(ValidationError, "retry_user_message_id"):
            ChatRequest(model_id="deepseek-chat", message="改后", conversation_id="conv-1", edit_user_message=True)

    def test_edit_cannot_reuse_original_answer(self):
        with self.assertRaisesRegex(ValidationError, "不能复用"):
            ChatRequest(
                model_id="deepseek-chat",
                message="改后",
                conversation_id="conv-1",
                retry_user_message_id=USER_MESSAGE_ID,
                retry_assistant_message_id=ASSISTANT_MESSAGE_ID,
                edit_user_message=True,
            )

    def test_edit_rejects_blank_message(self):
        with self.assertRaisesRegex(ValidationError, "不能为空"):
            ChatRequest(
                model_id="deepseek-chat",
                message="  ",
                conversation_id="conv-1",
                retry_user_message_id=USER_MESSAGE_ID,
                edit_user_message=True,
            )

    def test_send_route_forwards_edit_flag(self):
        chat_service = SimpleNamespace(process_message=AsyncMock(return_value=StreamingResponse(iter(()))))
        chat_request = ChatRequest(
            model_id="deepseek-chat",
            message="改后",
            conversation_id="conv-1",
            retry_user_message_id=USER_MESSAGE_ID,
            edit_user_message=True,
        )
        request = SimpleNamespace(state=SimpleNamespace(request_id="request-1"))

        asyncio.run(
            send_message(
                chat_request,
                request,
                chat_service=chat_service,
                current_user=SimpleNamespace(id="user-1", is_superuser=False, is_guest=False),
            )
        )

        kwargs = chat_service.process_message.await_args.kwargs
        self.assertTrue(kwargs["edit_user_message"])
        self.assertEqual(kwargs["retry_user_message_id"], USER_MESSAGE_ID)


class PrepareMessageEditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        db.add(UserModel(id="user-1", username="user-1"))
        db.add(ConversationModel(id="conv-1", user_id="user-1", title="编辑", model_id="deepseek-chat"))
        rows = [
            ("u1", "user", [{"type": "text", "id": "t1", "text": "第一问"}]),
            ("a1", "assistant", [{"type": "text", "id": "t2", "text": "第一答"}]),
            (
                "u2",
                "user",
                [
                    {"type": "text", "id": "t3", "text": "第二问"},
                    {
                        "type": "file",
                        "id": "f1",
                        "file_id": "file-1",
                        "filename": "a.pdf",
                        "mime_type": "application/pdf",
                    },
                ],
            ),
            ("a2", "assistant", [{"type": "text", "id": "t4", "text": "第二答"}]),
            ("u3", "user", [{"type": "text", "id": "t5", "text": "第三问"}]),
            ("a3", "assistant", [{"type": "text", "id": "t6", "text": "第三答"}]),
        ]
        db.add_all(
            MessageModel(id=mid, conversation_id="conv-1", role=role, content=content, sequence=index + 1)
            for index, (mid, role, content) in enumerate(rows)
        )
        db.commit()
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def _message_ids(self):
        db = self.Session()
        try:
            return [
                row.id
                for row in db.query(MessageModel)
                .filter(MessageModel.conversation_id == "conv-1")
                .order_by(MessageModel.sequence)
                .all()
            ]
        finally:
            db.close()

    def test_rewrites_question_keeps_attachment_and_drops_later_turns(self):
        db = self.Session()
        try:
            edited, preceding_ids = ConversationService(db).prepare_message_edit(
                conversation_id="conv-1",
                user_id="user-1",
                user_message_id="u2",
                text="改后的第二问",
            )
            db.commit()
        finally:
            db.close()

        self.assertEqual(preceding_ids, {"u1", "a1"})
        self.assertEqual(edited.id, "u2")
        self.assertEqual(edited.content[0].id, "t3")
        self.assertEqual(edited.content[0].text, "改后的第二问")
        self.assertIsInstance(edited.content[1], FileBlock)
        self.assertEqual(self._message_ids(), ["u1", "a1", "u2"])

    def test_rollback_restores_question_and_later_turns(self):
        db = self.Session()
        try:
            ConversationService(db).prepare_message_edit(
                conversation_id="conv-1",
                user_id="user-1",
                user_message_id="u2",
                text="改后的第二问",
            )
            db.rollback()
            stored = db.query(MessageModel).filter(MessageModel.id == "u2").one()
            self.assertEqual(stored.content[0]["text"], "第二问")
        finally:
            db.close()

        self.assertEqual(self._message_ids(), ["u1", "a1", "u2", "a2", "u3", "a3"])

    def test_rejects_assistant_target_and_other_users_conversation(self):
        db = self.Session()
        try:
            service = ConversationService(db)
            with self.assertRaises(ApiException) as not_user:
                service.prepare_message_edit(
                    conversation_id="conv-1", user_id="user-1", user_message_id="a2", text="改"
                )
            with self.assertRaises(ApiException) as not_owner:
                service.prepare_message_edit(
                    conversation_id="conv-1", user_id="user-2", user_message_id="u2", text="改"
                )
        finally:
            db.rollback()
            db.close()

        self.assertEqual(not_user.exception.status_code, 404)
        self.assertEqual(not_owner.exception.status_code, 404)
        self.assertEqual(self._message_ids(), ["u1", "a1", "u2", "a2", "u3", "a3"])


class ProcessMessageEditTests(unittest.TestCase):
    def _service(self):
        db = MagicMock()
        service = ChatService(db)
        service.file_repo = MagicMock()
        service.stream_handler = MagicMock()
        service.stream_handler.generate_to_redis = AsyncMock()
        service.conversation_service = MagicMock()
        earlier_user = Message(id="u1", sequence=1, role="user", content=[TextBlock(type="text", text="第一问")])
        earlier_answer = Message(id="a1", sequence=2, role="assistant", content=[TextBlock(type="text", text="答")])
        original = Message(id=USER_MESSAGE_ID, sequence=3, role="user", content=[TextBlock(type="text", text="原问题")])
        later = Message(id="a2", sequence=4, role="assistant", content=[TextBlock(type="text", text="旧回答")])
        service.conversation_service.get_conversation.return_value = Conversation(
            id="conv-1",
            user_id="user-1",
            title="编辑",
            model_id="saved/model",
            messages=[earlier_user, earlier_answer, original, later],
        )
        edited = Message(id=USER_MESSAGE_ID, sequence=3, role="user", content=[TextBlock(type="text", text="改后")])
        service.conversation_service.prepare_message_edit.return_value = (edited, {"u1", "a1"})
        service.conversation_service.reserve_message_sequence_pair.return_value = (11, 12)
        return service

    def _run(self, service, *, stream_meta=None, file_ids=None):
        def close_created_coroutine(coroutine):
            coroutine.close()
            return MagicMock()

        with (
            patch(
                "app.services.chat_service.llm_manager.resolve_model",
                return_value=("openai/saved-model", "openai", {}),
            ),
            patch(
                "app.services.chat_service.litellm_catalog.get_capabilities",
                return_value={"functionCalling": False, "agentTools": False, "vision": False},
            ),
            patch("app.services.chat_service.get_stream_meta", new=AsyncMock(return_value=stream_meta)),
            patch(
                "app.services.chat_service.init_stream",
                new=AsyncMock(return_value=StreamInitResult(ok=True)),
            ) as init_stream_mock,
            patch("app.services.chat_service.asyncio.create_task", side_effect=close_created_coroutine),
            patch("app.services.chat_service.register_task"),
        ):
            asyncio.run(
                service.process_message(
                    model_id="saved/model",
                    message="改后",
                    user_id="user-1",
                    conversation_id="conv-1",
                    retry_user_message_id=USER_MESSAGE_ID,
                    edit_user_message=True,
                    file_ids=file_ids,
                    stream=True,
                )
            )
        return init_stream_mock

    def test_edit_regenerates_from_edited_question_without_later_history(self):
        service = self._service()

        init_stream_mock = self._run(service)

        service.conversation_service.prepare_message_edit.assert_called_once_with(
            conversation_id="conv-1",
            user_id="user-1",
            user_message_id=USER_MESSAGE_ID,
            text="改后",
        )
        service.conversation_service.prepare_message_retry.assert_not_called()
        service.conversation_service.create_message.assert_not_called()
        task_id = init_stream_mock.await_args.args[4]
        service.conversation_service.claim_unanswered_user_generation.assert_called_once_with(
            conversation_id="conv-1",
            message_id=USER_MESSAGE_ID,
            task_id=task_id,
        )
        kwargs = service.stream_handler.generate_to_redis.call_args.kwargs
        self.assertEqual([message.id for message in kwargs["raw_messages"]], ["u1", "a1", USER_MESSAGE_ID])
        self.assertEqual(kwargs["raw_messages"][-1].content[0].text, "改后")
        self.assertEqual(kwargs["original_message"], "改后")
        self.assertEqual(kwargs["create_after_retry_user_id"], USER_MESSAGE_ID)
        self.assertFalse(kwargs["replace_on_success"])
        self.assertEqual(kwargs["run_attempt_kind"], "edit")

    def test_edit_rejected_while_answer_is_streaming(self):
        service = self._service()

        with self.assertRaises(ApiException) as raised:
            self._run(service, stream_meta={"status": "streaming"})

        self.assertEqual(raised.exception.status_code, 409)
        service.conversation_service.prepare_message_edit.assert_not_called()

    def test_edit_rejects_changed_attachments(self):
        service = self._service()

        with self.assertRaises(ApiException) as raised:
            self._run(service, file_ids=["file-other"])

        self.assertEqual(raised.exception.status_code, 409)
        service.stream_handler.generate_to_redis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
