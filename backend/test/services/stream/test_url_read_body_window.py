"""读页正文窗口不能被长导航耗尽，也不能误删非正文前缀。"""

import unittest
from copy import deepcopy
from unittest.mock import patch

from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import UrlReadHandler


class UrlReadBodyWindowTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("app.services.tool_handlers.url_read._tool_context_int", return_value=8000))

    def result(self, content, title="Nvidia’s $30B Perplexity Bet"):
        return ToolResult(
            status="success", data={"url": "https://finance.yahoo.com/news/report", "title": title, "content": content}
        )

    def wrap(self, body):
        return (
            "Title: Nvidia’s $30B Perplexity Bet\n\nURL Source: https://finance.yahoo.com/news/report\n\nMarkdown Content:\n"
            + body
        )

    def test_exact_article_title_after_long_navigation_reaches_context_without_mutating_raw_data(self):
        raw = self.wrap(
            "[导航](https://example.com)\n" * 1300
            + "# Nvidia’s $30B Perplexity Bet\n\n文章核心事实：投资用于算力租赁。"
        )
        result = self.result(raw)
        before = deepcopy(result.data)
        context = UrlReadHandler().format_llm_context(result, citation_numbers=[7])
        self.assertIn("文章核心事实：投资用于算力租赁。", context)
        self.assertNotIn("导航", context)
        self.assertIn('source_id="7"', context)
        self.assertEqual(result.data, before)

    def test_plain_page_without_markdown_wrapper_keeps_original_prefix(self):
        context = UrlReadHandler().format_llm_context(
            self.result("必须保留的摘要\n# Nvidia’s $30B Perplexity Bet\n正文")
        )
        self.assertIn("必须保留的摘要", context)

    def test_no_matching_title_does_not_remove_content(self):
        for heading in ("## Nvidia’s $30B Perplexity Bet", "# Nvidia’s $30B Perplexity Bet Analysis", "没有标题"):
            with self.subTest(heading=heading):
                context = UrlReadHandler().format_llm_context(
                    self.result(self.wrap("必须保留的摘要\n" + heading + "\n正文"))
                )
                self.assertIn("必须保留的摘要", context)

    def test_long_article_keeps_existing_truncation_marker(self):
        result = self.result(self.wrap("导航\n# Nvidia’s $30B Perplexity Bet\n" + "正文" * 5000 + "不可达的尾部"))
        context = UrlReadHandler().format_llm_context(result)
        self.assertNotIn("导航", context)
        self.assertNotIn("不可达的尾部", context)
        self.assertIn("truncated", context.lower())

    def test_title_normalization_handles_html_entities_and_whitespace(self):
        result = self.result(self.wrap("导航\n#  Nvidia&#8217;s   $30B Perplexity Bet  #\n正文"))
        context = UrlReadHandler().format_llm_context(result)
        self.assertNotIn("导航", context)
        self.assertIn("正文", context)

    def test_matching_heading_inside_code_fence_is_not_a_body_boundary(self):
        result = self.result(self.wrap("必须保留的摘要\n```markdown\n# Nvidia’s $30B Perplexity Bet\n```\n正文"))
        context = UrlReadHandler().format_llm_context(result)
        self.assertIn("必须保留的摘要", context)
