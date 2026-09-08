"""替代工具证据只能来自成功且有正文的实际结果。"""

import unittest

from app.services.stream.tool_recovery_evidence import (
    RecoveryEvidenceWorkset,
    has_recovery_evidence,
    is_grounded_recovery_answer,
)
from app.services.tool_handlers.base import ToolResult

URL = "https://example.org/forecast"


def block(*, status="success", ref_status="success", url=URL, index=1):
    return {
        "type": "search",
        "status": status,
        "source_refs": [{"kind": "search", "url": url, "status": ref_status, "citation_index": index}],
    }


def evidence(*, status="success", description="明天有骤雨。", tool_name="web_search"):
    result = ToolResult(
        status=status,
        data={"sources": [{"url": URL, "description": description}], "url": URL, "content": description},
    )
    workset = RecoveryEvidenceWorkset()
    workset.record_result(tool_name, result)
    return workset


class ToolRecoveryEvidenceTests(unittest.TestCase):
    def test_accept_explicit_actual_source(self):
        for answer in ["明天有骤雨。[1]", "明天有骤雨。⟦1⟧", f"明天有骤雨。[预报]({URL})"]:
            with self.subTest(answer=answer):
                assert is_grounded_recovery_answer(answer, [block()], evidence=evidence())

    def test_evidence_presence_is_independent_of_answer_citation_format(self):
        for answer in [
            "明天有骤雨。",
            "明天有骤雨。[9]",
            "明天有骤雨。[1][9]",
            "来源example.org",
            f"[链接]({URL}/invented)",
            "[1] https://invented.example/report",
        ]:
            with self.subTest(answer=answer):
                assert is_grounded_recovery_answer(answer, [block()], evidence=evidence())

    def test_empty_answer_is_not_a_recovery_answer(self):
        for answer in ("", "  \n"):
            with self.subTest(answer=answer):
                assert not is_grounded_recovery_answer(answer, [block()], evidence=evidence())

    def test_failed_results_cannot_become_evidence(self):
        for status in ["failed", "degraded"]:
            with self.subTest(status=status):
                assert not has_recovery_evidence([block()], evidence=evidence(status=status))
                assert not has_recovery_evidence([block(status=status)], evidence=evidence())
                assert not has_recovery_evidence([block(ref_status=status)], evidence=evidence())

    def test_metadata_or_empty_results_are_not_evidence(self):
        for description in ["", "  \n", None, URL]:
            with self.subTest(description=description):
                assert not has_recovery_evidence([block()], evidence=evidence(description=description))

    def test_successful_url_read_has_content_and_actual_reference(self):
        blocks = [
            {
                **block(),
                "type": "url_read",
                "source_refs": [{"kind": "url_read", "url": URL, "status": "success", "citation_index": 1}],
            }
        ]
        assert is_grounded_recovery_answer("有骤雨。[1]", blocks, evidence=evidence(tool_name="url_read"))

    def test_unrelated_url_or_tool_cannot_satisfy_gate(self):
        assert not has_recovery_evidence([block(url="https://example.org/other")], evidence=evidence())
        assert not has_recovery_evidence([block()], evidence=evidence(tool_name="weather_forecast"))

    def test_metadata_does_not_inherit_content_from_different_tool_kind(self):
        assert not has_recovery_evidence([block()], evidence=evidence(tool_name="url_read"))

    def test_runtime_pydantic_sources_and_content_blocks_match(self):
        from app.schemas.chat import SearchBlock, SearchSource, SourceReference

        workset = RecoveryEvidenceWorkset()
        workset.record_result(
            "web_search",
            ToolResult(
                status="success", data={"sources": [SearchSource(title="预报", url=URL, description="明天有骤雨。")]}
            ),
        )
        source_block = SearchBlock(
            type="search",
            query="明天预报",
            sources=[],
            source_refs=[SourceReference(kind="search", url=URL, citation_index=7)],
        )
        assert is_grounded_recovery_answer("明天有骤雨。[7]", [source_block], evidence=workset)

    def test_metadata_candidates_beyond_injected_summary_cap_are_not_evidence(self):
        workset = RecoveryEvidenceWorkset()
        sources = [{"url": f"https://example.org/{i}", "description": f"正文{i}"} for i in range(3)]
        for counts in ({"context_source_count": 1}, {"context_source_limit": 1}, {"context_source_count": 0}):
            with self.subTest(counts=counts):
                workset = RecoveryEvidenceWorkset()
                workset.record_result("web_search", ToolResult(status="success", data={"sources": sources, **counts}))
                self.assertFalse(
                    is_grounded_recovery_answer(
                        "声称具体事实[3]", [block(url="https://example.org/2", index=3)], evidence=workset
                    )
                )
        workset.record_result(
            "url_read", ToolResult(status="success", data={"url": "https://example.org/2", "content": "真正读到的正文"})
        )
        read_block = {
            "type": "url_read",
            "status": "success",
            "source_refs": [
                {"kind": "url_read", "url": "https://example.org/2", "status": "success", "citation_index": 3}
            ],
        }
        self.assertTrue(is_grounded_recovery_answer("读取后结论[3]", [read_block], evidence=workset))
