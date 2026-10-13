import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Conversation, ConversationFile, File, User
from app.db.repositories import FileRepository


class FileRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()

        self.session.add(
            User(
                id="user-123",
                username="tester",
                email="tester@example.com",
            )
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_create_file_persists_user_id(self):
        repo = FileRepository(self.session)

        saved = repo.create_file(
            {
                "id": "file-123",
                "user_id": "user-123",
                "filename": "file-123_note.txt",
                "original_filename": "note.txt",
                "mimetype": "text/plain",
                "size": 12,
                "path": "/tmp/file-123_note.txt",
                "status": "parsing",
                "processing_result": None,
            }
        )

        self.assertEqual(saved.id, "file-123")
        self.assertEqual(saved.user_id, "user-123")

    def test_conversation_parsed_file_content_only_returns_own_linked_processed_files(self):
        self.session.add(User(id="user-other", username="other", email="other@example.com"))
        for conversation_id, user_id in (("conv-1", "user-123"), ("conv-2", "user-123"), ("conv-o", "user-other")):
            self.session.add(Conversation(id=conversation_id, user_id=user_id, title="t", model_id="m"))
        for file_id, user_id, status, conversation_id in (
            ("doc-ok", "user-123", "processed", "conv-1"),
            ("doc-parsing", "user-123", "parsing", "conv-1"),
            ("doc-other-conv", "user-123", "processed", "conv-2"),
            ("doc-other-user", "user-other", "processed", "conv-o"),
        ):
            self.session.add(
                File(
                    id=file_id,
                    user_id=user_id,
                    filename=file_id,
                    original_filename=f"{file_id}.txt",
                    mimetype="text/plain",
                    size=1,
                    path=f"/tmp/{file_id}",
                    status=status,
                    parsed_content=f"{file_id} 正文",
                )
            )
            self.session.add(ConversationFile(conversation_id=conversation_id, file_id=file_id))
        self.session.commit()

        contents = FileRepository(self.session).get_conversation_parsed_file_content(
            ["doc-ok", "doc-parsing", "doc-other-conv", "doc-other-user"],
            user_id="user-123",
            conversation_id="conv-1",
        )

        self.assertEqual(contents, {"doc-ok": "doc-ok 正文"})


if __name__ == "__main__":
    unittest.main()
