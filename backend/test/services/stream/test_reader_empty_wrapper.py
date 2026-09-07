"""读取器包装元信息不能替代有效网页正文。"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.stream.research_evidence import ResearchEvidenceWorkset
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset
from app.services.tool_handlers.url_read import UrlReadHandler

URL = "https://www.reuters.com/world/example"
WARNING = "Warning: This page maybe requiring CAPTCHA, please make sure you are authorized to access this page."


class ReaderEmptyWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def execute_content(self, content):
        response = httpx.Response(
            200,
            json={
                "url": URL,
                "title": "reuters.com",
                "content": content,
                "content_length": len(content),
                "fetch_ms": 321,
                "attempts": 2,
            },
            request=httpx.Request("GET", "https://reader.example/read"),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with patch("app.services.external.reader_client.httpx.AsyncClient", return_value=client):
            return await UrlReadHandler().execute({"url": URL})

    async def test_captcha_wrapper_with_empty_body_is_failed_evidence(self):
        raw = f"Title: reuters.com\n\nURL Source: {URL}\n\n{WARNING}\n\nMarkdown Content:\n\n"
        result = await self.execute_content(raw)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.data["failure_kind"], "access_blocked")
        self.assertEqual(result.data["attempts"], 2)
        self.assertEqual(result.data["reader_duration_ms"], 321)
        self.assertIn(WARNING, result.data["reader_diagnostic"])
        self.assertNotIn("content", result.data)
        self.assertNotIn("reader_diagnostic", UrlReadHandler().sanitize_output_data_for_log(result))
        block = UrlReadHandler().build_content_block(result, "block", "log")
        self.assertEqual(block.source_refs, [])
        research = ResearchEvidenceWorkset()
        research.record_content_blocks([block])
        self.assertEqual(research.successful_read_urls, set())
        workset = RecoveryEvidenceWorkset()
        workset.record_result("url_read", result)
        self.assertEqual(workset.source_keys, set())
        context = UrlReadHandler().format_llm_context(result, citation_numbers=[3])
        self.assertIn("access_blocked", context)
        self.assertIn("alternative", context)
        self.assertNotIn("[3]", context)

    async def test_empty_body_without_captcha_is_empty_content(self):
        for raw in (" \n", f"Title: Page\nURL Source: {URL}\nMarkdown Content:\n\n"):
            with self.subTest(raw=raw):
                result = await self.execute_content(raw)
                self.assertEqual(result.status, "degraded")
                self.assertEqual(result.data["failure_kind"], "empty_content")

    async def test_article_about_captcha_or_nonempty_warned_body_remains_success(self):
        for raw in (
            "# CAPTCHA\n本文介绍如何设计验证码。",
            f"Title: Page\nURL Source: {URL}\n{WARNING}\nMarkdown Content:\n这里确实存在文章正文。",
            f"文章引用了读取器格式：\nURL Source: {URL}\nMarkdown Content:\n",
        ):
            with self.subTest(raw=raw):
                result = await self.execute_content(raw)
                self.assertEqual(result.status, "success")
                self.assertEqual(result.data["content"], raw)
