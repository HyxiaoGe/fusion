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

    def test_plain_article_after_long_navigation_reaches_context_without_mutating_raw_data(self):
        raw = (
            "- [站内导航](https://finance.yahoo.com/markets)\n" * 1300
            + "# Nvidia’s $30B Perplexity Bet\n\n文章核心事实：投资用于算力租赁。"
        )
        result = self.result(raw)
        before = deepcopy(result.data)
        context = UrlReadHandler().format_llm_context(result, citation_numbers=[7])
        self.assertIn("文章核心事实：投资用于算力租赁。", context)
        self.assertNotIn("站内导航", context)
        self.assertNotIn("truncated", context.lower())
        self.assertIn('source_id="7"', context)
        self.assertEqual(result.data, before)

    def test_plain_navigation_supports_linked_logo_lists_relative_links_and_crlf(self):
        raw = (
            "[![站点标志](https://cdn.example.com/logo.png)](https://finance.yahoo.com/)\r\n"
            "* [市场](/markets) | [新闻](news)\r\n"
            "  - [首页](//finance.yahoo.com/)\r\n\r\n---\r\n"
            "#  Nvidia&#8217;s   $30B Perplexity Bet  #\r\n\r\n正文事实。"
        )
        context = UrlReadHandler().format_llm_context(self.result(raw))
        self.assertIn("正文事实。", context)
        self.assertNotIn("站点标志", context)
        self.assertNotIn("市场", context)

    def test_plain_non_navigation_prefix_is_preserved(self):
        prefixes = (
            "必须保留的摘要\n[导航](/markets)\n",
            "| 参数 | 数值 |\n| --- | --- |\n| 内存 | 16 GB |\n",
            "- 必须保留的要点\n",
            "[目录](#details)\n",
            "[目录](https://finance.yahoo.com/news/report#details)\n",
            "[外部资料](https://example.com/report)\n",
            "[资料][report]\n\n[report]: https://finance.yahoo.com/report\n",
            "```markdown\n[资料](/report)\n```\n",
            "    [代码示例](/report)\n",
            "[导航](/markets)\n![必须保留的图表](/chart.png)\n",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                context = UrlReadHandler().format_llm_context(
                    self.result(prefix + "# Nvidia’s $30B Perplexity Bet\n正文事实。")
                )
                self.assertIn(prefix.strip(), context)

    def test_plain_link_directory_is_not_an_article_boundary(self):
        for marker in ("-", "1.", "1)"):
            with self.subTest(marker=marker):
                raw = f"[相关页面](/related)\n# Nvidia’s $30B Perplexity Bet\n{marker} [参考资料](/resources)\n"
                context = UrlReadHandler().format_llm_context(self.result(raw))
                self.assertIn("相关页面", context)
                self.assertIn("参考资料", context)

    def test_plain_article_with_table_body_reaches_context(self):
        raw = "[站内导航](/markets)\n# Nvidia’s $30B Perplexity Bet\n## 参数\n| 参数 | 数值 |\n| 内存 | 16 GB |\n"
        context = UrlReadHandler().format_llm_context(self.result(raw))
        self.assertNotIn("站内导航", context)
        self.assertIn("16 GB", context)

    def test_plain_title_or_fenced_heading_mismatch_keeps_navigation(self):
        for title, body in (
            ("", "# Nvidia’s $30B Perplexity Bet\n正文"),
            ("另一个标题", "# Nvidia’s $30B Perplexity Bet\n正文"),
            ("Nvidia’s $30B Perplexity Bet", "## Nvidia’s $30B Perplexity Bet\n正文"),
            ("Nvidia’s $30B Perplexity Bet", "```markdown\n# Nvidia’s $30B Perplexity Bet\n正文"),
            ("Nvidia’s $30B Perplexity Bet", "    # Nvidia’s $30B Perplexity Bet\n正文"),
        ):
            with self.subTest(title=title, body=body):
                context = UrlReadHandler().format_llm_context(self.result("[站内导航](/markets)\n" + body, title))
                self.assertIn("站内导航", context)

    def test_plain_body_window_keeps_configured_budget_and_truncation_marker(self):
        raw = "[站内导航](/markets)\n# Nvidia’s $30B Perplexity Bet\n核心事实。" + "正文" * 5000 + "不可达的尾部"
        with patch("app.services.tool_handlers.url_read._tool_context_int", return_value=100):
            context = UrlReadHandler().format_llm_context(self.result(raw))
        self.assertIn("核心事实。", context)
        self.assertNotIn("站内导航", context)
        self.assertNotIn("不可达的尾部", context)
        self.assertIn("truncated", context.lower())

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
