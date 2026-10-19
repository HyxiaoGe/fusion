import unittest

from app.schemas.chat import SearchBlock, SearchSourceSummary, SourceReference, UrlBlock
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    build_research_untrusted_context_messages,
    build_research_workset_prompt,
)


def _search_block(*, citation_index: int = 1) -> SearchBlock:
    return SearchBlock(
        type="search",
        query="官方资料",
        sources=[SearchSourceSummary(title="官方资料", url="https://example.com/report")],
        source_refs=[
            SourceReference(
                kind="search",
                title="官方资料",
                url="https://example.com/report",
                evidence_id="ev-web-report",
                citation_index=citation_index,
            )
        ],
        source_count=1,
    )


def _read_block(url: str, *, evidence_id: str, citation_index: int) -> UrlBlock:
    return UrlBlock(
        type="url_read",
        url=url,
        title=f"来源 {citation_index}",
        status="success",
        source_count=1,
        source_refs=[
            SourceReference(
                kind="url_read",
                title=f"来源 {citation_index}",
                url=url,
                evidence_id=evidence_id,
                citation_index=citation_index,
            )
        ],
    )


class ResearchEvidenceTests(unittest.TestCase):
    def test_unsuccessful_read_is_listed_as_read_failed_without_context(self):
        for status in ("failed", "degraded"):
            with self.subTest(status=status):
                workset = ResearchEvidenceWorkset()
                workset.record_content_blocks(
                    [
                        _search_block(),
                        UrlBlock(
                            type="url_read",
                            url="https://example.com/report",
                            title="读取未成功",
                            status=status,
                            source_count=0,
                            source_refs=[],
                            error_message="无法读取",
                        ),
                    ]
                )

                self.assertEqual(workset.successful_read_urls, set())
                self.assertEqual(workset.attempted_read_urls, {"https://example.com/report"})
                control = build_research_workset_prompt(workset)
                self.assertIn("[1] evidence_id=ev-web-report status=read_failed", control)
                self.assertNotIn("retry=", control)
                self.assertNotIn("example.com", control)
                self.assertEqual(build_research_untrusted_context_messages(workset), [])

    def test_source_reference_new_fields_are_optional_for_legacy_blocks(self):
        legacy = SourceReference(kind="search", title="旧来源", url="https://example.com/legacy")

        self.assertIsNone(legacy.evidence_id)
        self.assertIsNone(legacy.citation_index)

    def test_workset_keeps_bounded_safe_projection_and_deduplicates_urls(self):
        workset = ResearchEvidenceWorkset()
        workset.record_content_blocks(
            [
                _search_block(),
                _read_block("https://example.com/a?utm_source=test", evidence_id="ev-a", citation_index=2),
                _read_block("https://example.com/a", evidence_id="ev-a", citation_index=2),
            ],
            summaries={
                "ev-web-report": ("官方摘要", ["关键发现一"]),
                "ev-a": ("原文摘要", ["关键发现二"]),
            },
        )

        self.assertEqual(workset.successful_read_urls, {"https://example.com/a"})
        self.assertEqual(len(workset.sources), 2)
        prompt = build_research_workset_prompt(workset)
        self.assertIn("[1] evidence_id=ev-web-report status=candidate", prompt)
        self.assertIn("[2] evidence_id=ev-a status=read_success", prompt)
        self.assertNotIn("官方资料", prompt)
        self.assertNotIn("example.com", prompt)
        self.assertNotIn("关键发现一", prompt)

        context_messages = build_research_untrusted_context_messages(workset)
        self.assertTrue(all(message["role"] == "user" for message in context_messages))
        self.assertIn("官方摘要", context_messages[0]["content"])
        self.assertIn("关键发现一", context_messages[0]["content"])

    def test_late_high_index_read_source_is_retained_ahead_of_early_candidates(self):
        candidate_refs = [
            SourceReference(
                kind="search",
                title=f"候选 {index}",
                url=f"https://example.com/{index}",
                evidence_id=f"ev-{index}",
                citation_index=index,
            )
            for index in range(1, 14)
        ]
        workset = ResearchEvidenceWorkset()
        workset.record_content_blocks(
            [
                SearchBlock(
                    type="search",
                    query="候选",
                    sources=[SearchSourceSummary(title=ref.title, url=ref.url) for ref in candidate_refs],
                    source_refs=candidate_refs,
                    source_count=len(candidate_refs),
                ),
                _read_block("https://example.com/13", evidence_id="ev-13", citation_index=13),
                _read_block("https://example.com/12", evidence_id="ev-12", citation_index=12),
            ]
        )

        self.assertEqual(len(workset.sources), 12)
        self.assertIn("ev-13", workset.sources)
        self.assertIn("[13] evidence_id=ev-13 status=read_success", build_research_workset_prompt(workset))

    def test_new_unattempted_candidate_is_retained_ahead_of_twelve_failed_sources(self):
        failed_refs = [
            SourceReference(
                kind="search",
                title=f"失败候选 {index}",
                url=f"https://example.com/failed/{index}",
                evidence_id=f"ev-failed-{index}",
                citation_index=index,
            )
            for index in range(1, 13)
        ]
        workset = ResearchEvidenceWorkset()
        workset.record_content_blocks(
            [
                SearchBlock(
                    type="search",
                    query="旧候选",
                    sources=[SearchSourceSummary(title=ref.title, url=ref.url) for ref in failed_refs],
                    source_refs=failed_refs,
                    source_count=len(failed_refs),
                ),
                *[
                    UrlBlock(
                        type="url_read",
                        url=ref.url,
                        title=ref.title,
                        status="failed",
                        source_count=0,
                        source_refs=[],
                        error_message="无法读取",
                    )
                    for ref in failed_refs
                ],
                SearchBlock(
                    type="search",
                    query="新候选",
                    sources=[
                        SearchSourceSummary(
                            title="新候选",
                            url="https://example.com/new",
                        )
                    ],
                    source_refs=[
                        SourceReference(
                            kind="search",
                            title="新候选",
                            url="https://example.com/new",
                            evidence_id="ev-new",
                            citation_index=13,
                        )
                    ],
                    source_count=1,
                ),
            ]
        )

        self.assertEqual(len(workset.sources), 12)
        self.assertIn("ev-new", workset.sources)
        self.assertNotIn("ev-failed-12", workset.sources)
        self.assertIn("[13] evidence_id=ev-new status=candidate", build_research_workset_prompt(workset))


if __name__ == "__main__":
    unittest.main()
