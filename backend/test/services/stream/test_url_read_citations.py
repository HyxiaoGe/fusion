"""读页的模型引用编号必须与运行期来源及持久化编号一致。"""

import unittest

from app.schemas.chat import UrlBlock
from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import (
    _assign_search_citation_numbers,
    _attach_source_reference_metadata,
    _build_search_citation_registry,
)
from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import UrlReadHandler


class UrlReadCitationTests(unittest.TestCase):
    def record(self, url="https://example.org/a", *, status="success", content="有效正文"):
        return ToolExecutionRecord(
            tool_call={"id": "read-1", "name": "url_read"},
            result=ToolResult(status=status, data={"url": url, "title": "页面", "content": content}),
            handler=UrlReadHandler(),
            block_id="read-block",
            log_id="read-log",
        )

    def test_execution_record_passes_run_number_to_read_observation(self):
        context = self.record().format_llm_context(citation_numbers=[7])
        self.assertIn("[7]", context)
        self.assertIn('source_id="7"', context)
        self.assertNotIn("U1", context)
        self.assertIn("有效正文", context)

    def test_search_then_multiple_reads_keep_identity_through_persistence(self):
        blocks = [
            {
                "type": "search",
                "source_refs": [{"status": "success", "url": "https://example.org/a", "citation_index": 7}],
            }
        ]
        registry = _build_search_citation_registry(blocks)
        for url, expected in [("https://example.org/a", 7), ("https://example.org/b", 8)]:
            with self.subTest(url=url):
                record = self.record(url)
                numbers = _assign_search_citation_numbers(registry, record)
                self.assertEqual(numbers, [expected])
                context = record.format_llm_context(citation_numbers=numbers)
                self.assertIn(f"[{expected}]", context)
                enriched = _attach_source_reference_metadata(
                    record.build_content_block(), record=record, citation_numbers=numbers
                )
                restored = UrlBlock.model_validate_json(enriched.model_dump_json())
                self.assertEqual(restored.source_refs[0].citation_index, expected)
                used = build_used_final_answer_evidence(
                    content_blocks=[restored],
                    answer_text=f"结论[{expected}]",
                    evidence_policy="deep_research_v1",
                    allowed_citation_indexes={expected},
                )
                self.assertEqual([item["url"] for item in used], [url])

    def test_failed_read_does_not_advertise_citable_number(self):
        record = self.record(status="degraded", content="")
        context = record.format_llm_context(citation_numbers=[7])
        self.assertNotIn("[7]", context)
        self.assertNotIn("U1", context)
        self.assertIn("cannot be used as evidence", context)

    def test_unassigned_read_does_not_invent_first_source(self):
        context = self.record().format_llm_context()
        self.assertNotIn("[1]", context)
        self.assertNotIn("U1", context)
        self.assertIn("有效正文", context)
