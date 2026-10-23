import unittest
from unittest.mock import AsyncMock, patch

import httpx


class SearchClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_failures_reach_handler_without_becoming_empty_results(self):
        from app.services.tool_handlers.web_search import WebSearchHandler

        request = httpx.Request("POST", "https://search.example/search?token=private-marker")
        failures = [
            httpx.ReadTimeout("private-marker", request=request),
            httpx.ConnectError("private-marker", request=request),
            httpx.HTTPStatusError("private-marker", request=request, response=httpx.Response(503, request=request)),
        ]
        for failure in failures:
            with self.subTest(error_type=type(failure).__name__):
                client = AsyncMock()
                client.post.side_effect = failure
                with (
                    patch("app.services.external.search_client.httpx.AsyncClient") as factory,
                    patch("app.services.external.search_client.logger") as logger,
                ):
                    factory.return_value.__aenter__.return_value = client
                    result = await WebSearchHandler().execute({"query": "公开测试问题"})
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.data["error_code"], "search_unavailable")
                self.assertTrue(result.data["retryable"])
                self.assertNotIn("private-marker", str(logger.mock_calls))
                self.assertNotIn("private-marker", str(result))

    async def test_successful_empty_search_remains_empty_result(self):
        from app.services.tool_handlers.web_search import WebSearchHandler

        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"results": []}, request=httpx.Request("POST", "https://search.example/search")
        )
        with patch("app.services.external.search_client.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = await WebSearchHandler().execute({"query": "公开测试问题"})
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.error_message, "搜索返回空结果")
        self.assertNotIn("error_code", result.data)

    async def test_search_web_propagates_provider_metadata_to_sources(self):
        from app.services.external.search_client import search_web

        calls = []

        class FakeAsyncClient:
            def __init__(self, timeout: int):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, url: str, json: dict, headers: dict):
                calls.append({"url": url, "json": json, "timeout": self.timeout})
                return httpx.Response(
                    200,
                    json={
                        "provider": "brave",
                        "requested_provider": "firecrawl",
                        "result_provider": "brave",
                        "fallback_used": True,
                        "provider_chain": ["firecrawl", "brave"],
                        "results": [
                            {
                                "title": "Result",
                                "url": "https://example.com",
                                "description": "desc",
                                "favicon": "https://example.com/favicon.ico",
                            }
                        ],
                    },
                    request=httpx.Request("POST", url),
                )

        with patch("app.services.external.search_client.httpx.AsyncClient", FakeAsyncClient):
            sources = await search_web("test query", count=5, scene="web_search")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].requested_provider, "firecrawl")
        self.assertEqual(sources[0].result_provider, "brave")
        self.assertTrue(sources[0].fallback_used)
        self.assertEqual(sources[0].provider_chain, ["firecrawl", "brave"])
        self.assertIsNone(calls[0]["json"]["freshness"])

    async def test_search_web_passes_domain_filters_and_recency_freshness(self):
        from app.services.external.search_client import search_web

        calls = []

        class FakeAsyncClient:
            def __init__(self, timeout: int):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, url: str, json: dict, headers: dict):
                calls.append({"url": url, "json": json, "headers": headers})
                return httpx.Response(200, json={"results": []}, request=httpx.Request("POST", url))

        with patch("app.services.external.search_client.httpx.AsyncClient", FakeAsyncClient):
            await search_web("q", count=8, domains=["openai.com"], recency_days=30, scene="video_search")

        self.assertEqual(calls[0]["json"]["count"], 8)
        self.assertEqual(calls[0]["json"]["domain_filters"], ["openai.com"])
        self.assertEqual(calls[0]["json"]["freshness"], "pm")
        self.assertEqual(calls[0]["url"], "http://agent-tools-gateway:8100/search/search")
        self.assertEqual(calls[0]["headers"]["X-Caller-Scene"], "video_search")
        self.assertTrue(calls[0]["headers"]["Authorization"].startswith("Bearer "))

    async def test_search_images_pins_provider_whose_format_handler_accepts(self):
        from app.services.external.search_client import search_images
        from app.services.tool_handlers.image_search import IMAGE_RESULT_PROVIDERS

        calls = []

        class FakeAsyncClient:
            def __init__(self, timeout: int):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, url: str, json: dict, headers: dict):
                calls.append({"url": url, "json": json, "headers": headers})
                return httpx.Response(
                    200,
                    json={"result_provider": "firecrawl", "results": []},
                    request=httpx.Request("POST", url),
                )

        with patch("app.services.external.search_client.httpx.AsyncClient", FakeAsyncClient):
            await search_images("猫", count=4)

        self.assertEqual(calls[0]["json"]["type"], "image")
        self.assertIn(calls[0]["json"]["provider"], IMAGE_RESULT_PROVIDERS)
        self.assertEqual(calls[0]["headers"]["X-Caller-Scene"], "image_search")
