"""交付物文档：服务的版本与片段修改、Agent 工具的副作用边界、读取接口的归属校验。"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.db.database import Base  # noqa: E402
from app.db.models import Conversation, User  # noqa: E402
from app.schemas.chat import DocumentBlock, SearchBlock, UrlBlock, WeatherResultsBlock  # noqa: E402
from app.schemas.content_block_registry import (  # noqa: E402
    deserialize_content_blocks,
    is_registered_rich_content_block,
)
from app.schemas.document import DocumentSource  # noqa: E402
from app.services.documents.service import (  # noqa: E402
    MAX_DOCUMENT_CONTENT_CHARS,
    DocumentEdit,
    DocumentError,
    DocumentService,
    DocumentWriteContext,
    apply_document_edits,
)
from app.services.documents.sources import document_sources_from_blocks  # noqa: E402
from app.services.mcp.tool_contract import max_tool_argument_json_bytes  # noqa: E402
from app.services.stream.product_result_answer import has_product_result_blocks  # noqa: E402
from app.services.stream.tool_context import ToolRuntimeContext  # noqa: E402
from app.services.tool_handlers.document import (  # noqa: E402
    DocumentToolBinding,
    build_create_document_tool,
    build_document_tool_handlers,
    build_edit_document_tool,
)

GUIDE = """# 香港三天两夜

:::stats
- 总预算: ¥3,500-4,300
:::

## D1

:::timeline
- **09:00-11:05** 深圳出发 → 油麻地
:::
"""


def _weather_block() -> WeatherResultsBlock:
    return WeatherResultsBlock(
        type="weather_results",
        schema_version=1,
        provider="amap",
        status="degraded",
        limitations=["仅含 1 天预报"],
        query="香港",
        resolved_location="香港",
        day_count=1,
        forecast_days=[
            {
                "date": "2026-10-16",
                "weekday": 5,
                "day_weather": "晴",
                "night_weather": "多云",
                "high_c": 29,
                "low_c": 24,
            }
        ],
        fetched_at=datetime(2026, 10, 2, 8, 0, tzinfo=UTC),
    )


def _source(label: str, *, kind: str = "weather") -> DocumentSource:
    return DocumentSource(kind=kind, label=label, provider="高德地图", fetched_at=datetime(2026, 10, 2, tzinfo=UTC))


class _DatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.Session()
        db.add_all(
            [
                User(id="user-1", username="alice", email="alice@example.com"),
                User(id="user-2", username="bob", email="bob@example.com"),
                Conversation(id="conv-1", user_id="user-1", title="攻略", model_id="m"),
                Conversation(id="conv-2", user_id="user-2", title="别人的", model_id="m"),
            ]
        )
        db.commit()
        db.close()
        self.context = DocumentWriteContext(
            conversation_id="conv-1", user_id="user-1", message_id="msg-1", run_id="run-1"
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def service(self) -> DocumentService:
        return DocumentService(self.Session())


class DocumentServiceTests(_DatabaseCase):
    def test_create_then_edit_keeps_immutable_versions_and_merges_sources(self):
        created = self.service().create(
            self.context, title="  香港  攻略 ", content=GUIDE, sources=[_source("香港天气")]
        )
        self.assertEqual((created.version, created.title), (1, "香港 攻略"))

        edited = self.service().edit(
            self.context,
            document_id=created.document_id,
            edits=[DocumentEdit(old_text="深圳出发 → 油麻地", new_text="深圳出发 → 中环")],
            change_summary="D1 改去中环",
            sources=[_source("福田口岸 → 中环", kind="route"), _source("香港天气")],
        )
        self.assertEqual(edited.version, 2)
        self.assertIn("深圳出发 → 中环", edited.content)
        self.assertEqual([source.label for source in edited.sources], ["福田口岸 → 中环", "香港天气"])

        first = self.service().get_version(created.document_id, user_id="user-1", version=1)
        self.assertIn("深圳出发 → 油麻地", first.content)
        detail = self.service().get_detail(created.document_id, user_id="user-1")
        self.assertEqual(detail.current_version, 2)
        self.assertEqual([item.change_summary for item in detail.versions], [None, "D1 改去中环"])

    def test_edits_require_exactly_one_occurrence_and_report_failing_index(self):
        content = "A 段\nB 段\nB 段\n"
        with self.assertRaises(DocumentError) as missing:
            apply_document_edits(content, [DocumentEdit("A 段", "甲"), DocumentEdit("C 段", "丙")])
        self.assertEqual((missing.exception.code, missing.exception.edit_index), ("edit_old_text_not_found", 1))
        with self.assertRaises(DocumentError) as ambiguous:
            apply_document_edits(content, [DocumentEdit("B 段", "乙")])
        self.assertEqual(ambiguous.exception.code, "edit_old_text_not_unique")
        self.assertEqual(apply_document_edits(content, [DocumentEdit("A 段\nB", "乙")]), "乙 段\nB 段\n")

    def test_edit_rejects_other_conversations_no_change_and_ambiguous_mode(self):
        created = self.service().create(self.context, title="攻略", content=GUIDE)
        other = DocumentWriteContext(conversation_id="conv-2", user_id="user-2")
        cases = [
            (other, {"edits": [DocumentEdit("D1", "D2")]}, "document_not_found"),
            (self.context, {"edits": [DocumentEdit("D1", "D1")]}, "edit_no_change"),
            (self.context, {"edits": [DocumentEdit("D1", "D2")], "content": "x"}, "edit_mode_invalid"),
            (self.context, {}, "edit_mode_invalid"),
        ]
        for context, kwargs, code in cases:
            with self.subTest(code=code), self.assertRaises(DocumentError) as raised:
                self.service().edit(context, document_id=created.document_id, **kwargs)
            self.assertEqual(raised.exception.code, code)

    def test_create_validates_ownership_and_limits(self):
        cases = [
            (DocumentWriteContext(conversation_id="conv-2", user_id="user-1"), "攻略", GUIDE, "conversation_not_found"),
            (self.context, " ", GUIDE, "title_required"),
            (self.context, "攻略", "\n\n", "content_required"),
            (self.context, "攻略", "字" * (MAX_DOCUMENT_CONTENT_CHARS + 1), "content_too_long"),
        ]
        for context, title, content, code in cases:
            with self.subTest(code=code), self.assertRaises(DocumentError) as raised:
                self.service().create(context, title=title, content=content)
            self.assertEqual(raised.exception.code, code)

    def test_other_users_cannot_read(self):
        created = self.service().create(self.context, title="攻略", content=GUIDE)
        with self.assertRaises(DocumentError):
            self.service().get_detail(created.document_id, user_id="user-2")


class DocumentToolTests(_DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _DatabaseCase.setUp(self)
        self.handlers = build_document_tool_handlers(
            DocumentToolBinding(
                conversation_id="conv-1",
                user_id="user-1",
                session_factory=self.Session,
                message_id="msg-1",
                run_id="run-1",
            )
        )

    async def test_create_and_edit_produce_document_blocks_and_brief_model_context(self):
        create = self.handlers["create_document"]
        weather = _weather_block()
        runtime_context = ToolRuntimeContext(content_blocks=(weather,))
        created = await create.execute_with_runtime_context({"title": "香港攻略", "content": GUIDE}, runtime_context)
        self.assertEqual(created.status, "success")
        block = create.build_content_block(created, "blk-1", "log-1")
        self.assertIsInstance(block, DocumentBlock)
        self.assertEqual((block.version, block.operation, block.char_count), (1, "created", len(GUIDE)))
        context = create.format_llm_context(created)
        self.assertIn(created.data["document_id"], context)
        self.assertNotIn("深圳出发", context)

        edit = self.handlers["edit_document"]
        edited = await edit.execute(
            {
                "document_id": created.data["document_id"],
                "edits": [{"old_text": "油麻地", "new_text": "中环"}],
                "change_summary": "改去中环",
            }
        )
        self.assertEqual((edited.status, edited.data["version"]), ("success", 2))
        edited_block = edit.build_content_block(edited, "blk-2", "log-2")
        self.assertEqual((edited_block.operation, edited_block.change_summary), ("edited", "改去中环"))
        stored = self.service().get_version(created.data["document_id"], user_id="user-1")
        self.assertEqual([(source.kind, source.label) for source in stored.sources], [("weather", "香港")])
        self.assertEqual(stored.sources[0].fetched_at, weather.fetched_at)

    async def test_failures_return_actionable_context_without_block(self):
        edit = self.handlers["edit_document"]
        created = await self.handlers["create_document"].execute({"title": "攻略", "content": GUIDE})
        failed = await edit.execute(
            {
                "document_id": created.data["document_id"],
                "edits": [{"old_text": "不存在的段落", "new_text": "x"}],
                "change_summary": "改",
            }
        )
        self.assertEqual(failed.status, "failed")
        self.assertIsNone(edit.build_content_block(failed, "blk", "log"))
        context = edit.format_llm_context(failed)
        self.assertIn("edit_old_text_not_found", context)
        self.assertIn("Failing edit index: 0", context)
        malformed = await edit.execute(
            {"document_id": created.data["document_id"], "edits": "x", "change_summary": "改"}
        )
        self.assertEqual(malformed.data["error_code"], "edits_invalid")

    async def test_source_collection_failure_does_not_block_write(self):
        class BrokenContext:
            @property
            def content_blocks(self):
                raise RuntimeError("boom")

        handler = self.handlers["create_document"]
        result = await handler.execute_with_runtime_context({"title": "攻略", "content": GUIDE}, BrokenContext())
        self.assertEqual((result.status, result.data["source_count"]), ("success", 0))

    def test_sources_only_come_from_successful_structured_results(self):
        search = SearchBlock(
            type="search",
            query="香港 10 月活动",
            sources=[{"title": "香港旅游局", "url": "https://www.discoverhongkong.com/"}],
            result_provider="tavily",
        )
        failed_url = UrlBlock(type="url_read", url="https://example.com/x", status="failed")
        sources = document_sources_from_blocks([_weather_block(), search, failed_url, object()])
        self.assertEqual(
            [(source.kind, source.label, source.url) for source in sources],
            [("weather", "香港", None), ("web", "香港旅游局", "https://www.discoverhongkong.com/")],
        )

    def test_logs_and_events_never_carry_document_body(self):
        handler = self.handlers["edit_document"]
        args = {"document_id": "d", "content": GUIDE, "edits": [{"old_text": "a", "new_text": "b"}], "title": "t"}
        for sanitized in (handler.sanitize_input_params_for_log(args), handler.sanitize_input_params_for_event(args)):
            self.assertEqual(
                sanitized, {"document_id": "d", "title": "t", "content_chars": len(GUIDE), "edit_count": 1}
            )
        self.assertFalse(handler.supports_automatic_retry)

    def test_document_block_is_rich_content_but_not_a_product_result(self):
        block = {
            "type": "document",
            "id": "blk-1",
            "schema_version": 1,
            "document_id": "doc-1",
            "version": 2,
            "title": "攻略",
            "format": "markdown",
            "operation": "edited",
            "char_count": 10,
        }
        [decoded] = deserialize_content_blocks([block])
        self.assertIsInstance(decoded, DocumentBlock)
        self.assertTrue(is_registered_rich_content_block(decoded))
        self.assertFalse(has_product_result_blocks([decoded]))

    def test_tool_definitions_and_argument_budget(self):
        create = build_create_document_tool()["function"]
        edit = build_edit_document_tool()["function"]
        self.assertEqual(create["parameters"]["required"], ["title", "content"])
        self.assertEqual(edit["parameters"]["required"], ["document_id", "change_summary"])
        self.assertIn(":::timeline", create["description"])
        self.assertGreater(max_tool_argument_json_bytes("create_document"), MAX_DOCUMENT_CONTENT_CHARS * 6)
        self.assertEqual(max_tool_argument_json_bytes("web_search"), 64 * 1024)


class DocumentApiTests(_DatabaseCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)

    def setUp(self) -> None:
        super().setUp()
        from app.api.deps import get_current_user, get_db

        self.db = self.Session()
        self.current_user = SimpleNamespace(
            id="user-1", username="alice", email="alice@example.com", is_superuser=False
        )
        self.main.app.dependency_overrides[get_current_user] = lambda: self.current_user
        self.main.app.dependency_overrides[get_db] = lambda: self.db
        created = self.service().create(self.context, title="攻略", content=GUIDE, sources=[_source("香港天气")])
        self.service().edit(
            self.context,
            document_id=created.document_id,
            edits=[DocumentEdit("油麻地", "中环")],
            change_summary="改去中环",
        )
        self.document_id = created.document_id

    def tearDown(self) -> None:
        self.main.app.dependency_overrides.clear()
        self.db.close()
        super().tearDown()

    def test_detail_lists_versions_and_content_reads_specific_version(self):
        detail = self.client.get(f"/api/documents/{self.document_id}").json()["data"]
        self.assertEqual(detail["current_version"], 2)
        self.assertEqual([item["version"] for item in detail["versions"]], [1, 2])

        latest = self.client.get(f"/api/documents/{self.document_id}/content").json()["data"]
        self.assertIn("中环", latest["content"])
        self.assertEqual(latest["sources"][0]["label"], "香港天气")
        first = self.client.get(f"/api/documents/{self.document_id}/content", params={"version": 1}).json()["data"]
        self.assertIn("油麻地", first["content"])

    def test_other_user_and_missing_version_are_not_found(self):
        self.current_user = SimpleNamespace(id="user-2", username="bob", email="bob@example.com", is_superuser=False)
        self.assertEqual(self.client.get(f"/api/documents/{self.document_id}").status_code, 404)
        self.current_user = SimpleNamespace(
            id="user-1", username="alice", email="alice@example.com", is_superuser=False
        )
        response = self.client.get(f"/api/documents/{self.document_id}/content", params={"version": 9})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
