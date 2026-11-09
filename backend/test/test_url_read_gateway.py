"""url_read 经网关调用读取服务的 read_page：定义拼接、参数校验、转发、结果映射、失败说明与预读。"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services.external.agent_tools_mcp import GatewayToolOutcome, GatewayToolSpec
from app.services.security.url_policy import UrlPolicyResult
from app.services.stream import persistence as persistence_module
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence
from app.services.tool_handlers import url_read as url_read_module
from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import PageText, UrlReadHandler, build_url_read_tool

SERVICE_DESCRIPTION = "Read one web page and get its text as Markdown, with its title."
READ_PAGE_TOOL = {
    "name": "read_page",
    "description": SERVICE_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"description": "The complete http:// or https:// URL to read.", "type": "string"},
            "full_page": {"default": False, "description": "Read the whole page.", "type": "boolean"},
        },
        "required": ["url"],
    },
}
FAVICON = "https://www.google.com/s2/favicons?sz=32&domain=example.com"


def read_outcome(
    content: str,
    *,
    url: str = "https://example.com/a",
    title: str = "Example Article",
    favicon: str | None = FAVICON,
    truncated: bool = False,
    extracted: bool = False,
    note: str | None = None,
    provider: str = "jina",
) -> GatewayToolOutcome:
    """read_page 的成功答复，供其他测试代替网关。"""
    data: dict = {"ok": True, "url": url, "title": title, "content": content, "content_chars": len(content)}
    if truncated:
        data["truncated"] = True
    if extracted:
        data["extracted"] = True
    if note:
        data["note"] = note
    meta: dict = {"provider": provider, "attempts": 1, "fetch_ms": 900}
    if favicon:
        meta["favicon"] = favicon
    data["meta"] = meta
    model_payload = {key: value for key, value in data.items() if key != "meta"}
    return GatewayToolOutcome(ok=True, data=data, model_text=json.dumps(model_payload))


def read_failure(code: str, text: str, *, retryable: bool = False, status: int | None = None) -> GatewayToolOutcome:
    error: dict = {"code": code, "message": code, "retryable": retryable}
    if status is not None:
        error["status"] = status
    return GatewayToolOutcome(ok=False, model_text=text, error_code=code, error=error)


class _StaticReaderCatalog:
    def spec(self, service: str, tool_name: str) -> GatewayToolSpec | None:
        if (service, tool_name) == ("reader", "read_page"):
            return GatewayToolSpec(service="reader", **READ_PAGE_TOOL)
        return None


class _EmptyCatalog:
    def spec(self, service: str, tool_name: str) -> GatewayToolSpec | None:
        return None


def patch_reader_catalog():
    """让 url_read 按服务的完整参数（url、full_page）校验，代替 Run 开始前读到的目录。"""
    return patch.object(url_read_module, "gateway_tool_catalog", return_value=_StaticReaderCatalog())


def patch_gateway(outcome: GatewayToolOutcome):
    return patch.object(url_read_module, "call_gateway_tool", new=AsyncMock(return_value=outcome))


class UrlReadDefinitionTests(unittest.TestCase):
    def test_definition_uses_service_description_then_product_notes(self):
        tool = build_url_read_tool(_StaticReaderCatalog())["function"]

        self.assertEqual(tool["name"], "url_read")
        self.assertTrue(tool["description"].startswith(SERVICE_DESCRIPTION + "\n\n"))
        self.assertIn("When to read a page:", tool["description"])
        self.assertIn("numbered web_context source [n]", tool["description"])
        for phrase in ("official sources", "primary announcements", "Deprioritize videos, forums"):
            self.assertIn(phrase, tool["description"])
        self.assertEqual(set(tool["parameters"]["properties"]), {"url", "full_page"})
        self.assertEqual(tool["parameters"]["required"], ["url"])

    def test_without_catalog_announces_minimal_definition_with_product_notes(self):
        tool = build_url_read_tool(_EmptyCatalog())["function"]

        self.assertEqual(tool["name"], "url_read")
        self.assertTrue(tool["description"].startswith("Read one web page and get its text, with its title.\n\n"))
        self.assertIn("When to read a page:", tool["description"])
        self.assertEqual(tool["parameters"]["properties"], {"url": {"type": "string"}})
        self.assertFalse(tool["parameters"]["additionalProperties"])


class UrlReadArgumentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch_reader_catalog())

    def test_shape_follows_service_schema(self):
        handler = UrlReadHandler()

        self.assertEqual(handler.validate_arguments({"url": "https://example.com", "full_page": True}), [])
        # 旧版的 reason 参数已经不在服务的 schema 里，交回模型改参，不悄悄丢掉
        self.assertTrue(handler.validate_arguments({"url": "https://example.com", "reason": "核对"}))
        self.assertTrue(handler.validate_arguments({}))
        self.assertTrue(handler.validate_arguments({"url": "https://example.com", "full_page": "yes"}))

    def test_url_value_is_left_to_the_service(self):
        self.assertEqual(UrlReadHandler().validate_arguments({"url": "not a url"}), [])


class UrlReadExecuteTests(unittest.TestCase):
    def _execute(self, outcome: GatewayToolOutcome, args: dict | None = None) -> tuple[ToolResult, AsyncMock]:
        with patch_gateway(outcome) as call:
            result = asyncio.run(UrlReadHandler().execute(args or {"url": "https://example.com/a"}))
        return result, call

    def test_model_arguments_are_forwarded_to_read_page_unchanged(self):
        _result, call = self._execute(read_outcome("正文"), {"url": "https://example.com/a", "full_page": True})

        call.assert_awaited_once()
        self.assertEqual(
            call.await_args.args, ("reader", "read_page", {"url": "https://example.com/a", "full_page": True})
        )
        self.assertEqual(call.await_args.kwargs["scene"], "url_read")
        self.assertEqual(call.await_args.kwargs["min_call_timeout_seconds"], 15.0)

    def test_success_maps_page_card_and_reader_metadata(self):
        note = "Content truncated: showing the first 8000 of 12000 characters."
        result, _call = self._execute(read_outcome("正文" * 10, truncated=True, extracted=True, note=note))

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["url"], "https://example.com/a")
        self.assertEqual(result.data["title"], "Example Article")
        self.assertEqual(result.data["content"], "正文" * 10)
        self.assertEqual(result.data["favicon"], FAVICON)
        self.assertEqual(result.data["note"], note)
        self.assertTrue(result.data["truncated"])
        self.assertTrue(result.data["extracted"])
        self.assertEqual(result.data["provider"], "jina")
        self.assertEqual(result.data["reader_fetch_ms"], 900)
        block = UrlReadHandler().build_content_block(result, "blk", "log")
        self.assertEqual(block.favicon, FAVICON)
        self.assertEqual([ref.url for ref in block.source_refs], ["https://example.com/a"])

    def test_service_failure_keeps_service_text_for_the_model(self):
        text = "The page does not exist (the site returned HTTP 404). Do not read this URL again."
        result, _call = self._execute(read_failure("page_not_found", text, status=404))

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.error_message, "网页不存在（链接已失效或地址有误），已跳过该来源")
        self.assertEqual(result.data["failure_kind"], "page_not_found")
        self.assertEqual(result.data["upstream_status"], 404)
        self.assertFalse(result.data["retryable"])
        context = UrlReadHandler().format_llm_context(result, citation_numbers=[3])
        self.assertEqual(context, f"Read URL: https://example.com/a\n{text}")
        self.assertEqual(UrlReadHandler().build_content_block(result, "blk", "log").source_refs, [])

    def test_user_failure_texts_keep_frontend_suffix(self):
        # 前端 getUrlReadFailureReason 只认「，已跳过该来源」结尾的说明，其余一律显示「未能读取」
        from app.services.tool_handlers import url_read as module

        for text in (*module._USER_FAILURE_MESSAGES.values(), module._DEFAULT_USER_FAILURE_MESSAGE):
            self.assertTrue(text.endswith("，已跳过该来源"), text)

    def test_failed_read_is_not_evidence(self):
        result, _call = self._execute(read_failure("access_blocked", "The site blocked automated reading."))
        block = UrlReadHandler().build_content_block(result, "blk", "log")
        evidence = RecoveryEvidenceWorkset()
        evidence.record_result("url_read", result)

        self.assertFalse(has_recovery_evidence([block], evidence=evidence))

    def test_gateway_limit_without_service_text_reads_as_quota_exhausted(self):
        with self.assertLogs("app", level="WARNING"):
            result, _call = self._execute(GatewayToolOutcome(ok=False, error_code="rate_limited"))

        self.assertEqual(result.data["failure_kind"], "quota_exhausted")
        self.assertEqual(result.error_message, "今日网页读取额度已用完，已跳过该来源")
        context = UrlReadHandler().format_llm_context(result)
        self.assertIn("used up its quota for today", context)
        self.assertIn("Do not keep trying other pages", context)

    def test_other_errors_without_service_text_use_fusion_unavailable_text(self):
        with self.assertLogs("app", level="WARNING"):
            result, _call = self._execute(GatewayToolOutcome(ok=False, error_code="call_timeout"))

        self.assertEqual(result.error_message, "网页暂时无法读取，已跳过该来源")
        self.assertIn("cannot be used as evidence", UrlReadHandler().format_llm_context(result))

    def test_success_without_content_is_not_a_read(self):
        with self.assertLogs("app", level="WARNING"):
            result, _call = self._execute(read_outcome("  "))

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.data["failure_kind"], "empty_content")

    def test_failure_url_is_redacted_for_logs_and_cards(self):
        url = "https://example.com/a?token=secret-value"
        with patch_gateway(read_failure("unsafe_url", "Not read: The URL cannot be read.")):
            result = asyncio.run(UrlReadHandler().execute({"url": url}))

        self.assertNotIn("secret-value", result.data["url"])
        self.assertNotIn("secret-value", json.dumps(UrlReadHandler().sanitize_output_data_for_log(result)))

    def test_log_output_keeps_only_safe_fields(self):
        result, _call = self._execute(read_outcome("正文", note="note"))
        safe = UrlReadHandler().sanitize_output_data_for_log(result)

        self.assertNotIn("content", safe)
        self.assertNotIn("note", safe)
        self.assertEqual(safe["url"], "https://example.com/a")
        self.assertEqual(safe["provider"], "jina")


class UrlReadContextTests(unittest.TestCase):
    def test_page_text_is_numbered_and_wrapped_with_service_note_outside(self):
        note = "Content truncated: showing the first 8000 of 9000 characters."
        with patch_gateway(read_outcome("正" * 8000, title="<b>标题</b>", note=note)):
            result = asyncio.run(UrlReadHandler().execute({"url": "https://example.com/a"}))

        context = UrlReadHandler().format_llm_context(result, citation_numbers=[7])
        before, _, wrapped = context.partition("\n[7]\n")
        self.assertEqual(before, "Read URL: https://example.com/a")
        self.assertIn('source_id="7"', wrapped)
        self.assertIn("&lt;b&gt;标题&lt;/b&gt;", wrapped)
        self.assertIn("正" * 8000, wrapped)
        self.assertTrue(context.endswith(f"Reader note: {note}"))
        self.assertLess(context.index("</web_context>"), context.index("Reader note:"))

    def test_budget_limit_says_the_page_was_not_read(self):
        result = ToolResult(status="degraded", data={"url": "https://example.com/a", "budget_limited": True})

        context = UrlReadHandler().format_llm_context(result)

        self.assertIn("page-reading limit for this answer has been reached", context)


class UrlPrefetchTests(unittest.TestCase):
    policy = UrlPolicyResult(
        allowed=True,
        normalized_url="https://example.com/a",
        reason="ok",
        safe_log_url="https://example.com/a",
    )

    def test_prefetch_reads_through_read_page(self):
        with patch_gateway(read_outcome("网页正文", extracted=True, note="Main text was extracted.")) as call:
            page = asyncio.run(
                persistence_module.read_url_for_context(policy=self.policy, detected_url="https://EXAMPLE.com/a")
            )

        self.assertEqual(call.await_args.args, ("reader", "read_page", {"url": "https://example.com/a"}))
        self.assertEqual(call.await_args.kwargs["scene"], "url_context")
        self.assertEqual(
            page, PageText("https://example.com/a", "Example Article", "网页正文", FAVICON, "Main text was extracted.")
        )
        message = persistence_module.build_url_context_message(
            read_result=page, policy=self.policy, detected_url="https://example.com/a"
        )
        self.assertEqual(message["role"], "user")
        self.assertTrue(message["content"].endswith("Reader note: Main text was extracted."))

    def test_prefetch_failure_returns_none(self):
        with patch_gateway(read_failure("timeout", "The page did not load in time.", retryable=True)):
            page = asyncio.run(
                persistence_module.read_url_for_context(policy=self.policy, detected_url="https://example.com/a")
            )

        self.assertIsNone(page)


if __name__ == "__main__":
    unittest.main()
