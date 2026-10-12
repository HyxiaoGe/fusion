import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.content_block_registry import CONTENT_BLOCK_REGISTRY
from app.services.tool_handlers import get_handler
from app.services.tool_handlers.image_search import ImageSearchHandler, image_result_id

FIRECRAWL_RESULTS = [
    {
        "title": "iPhone 17 Pro - 技术规格",
        "url": "https://cdsassets.apple.com/hero.png",
        "description": "https://www.apple.com/iphone-17-pro/specs/",
    },
    # 同一张图重复返回只保留一次。
    {"title": "重复", "url": "https://cdsassets.apple.com/hero.png", "description": "https://other.example/"},
    # http 图片在 https 主站加载不了，丢弃。
    {"title": "http 图", "url": "http://insecure.example/a.jpg", "description": "https://insecure.example/"},
    {"title": "无来源", "url": "https://img.example/b.webp", "description": ""},
]


class ImageSearchHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def _execute(self, response, args=None):
        with patch(
            "app.services.tool_handlers.image_search.search_images",
            new=AsyncMock(return_value=response),
        ) as search:
            result = await ImageSearchHandler().execute(args or {"query": "iPhone 17 Pro 外观"})
        return result, search

    async def test_normalizes_images_with_stable_ids_and_sources(self):
        result, search = await self._execute({"result_provider": "firecrawl", "results": FIRECRAWL_RESULTS})

        search.assert_awaited_once_with("iPhone 17 Pro 外观", count=6)
        self.assertEqual(result.status, "success")
        self.assertEqual(
            result.data["images"],
            [
                {
                    "id": image_result_id("https://cdsassets.apple.com/hero.png"),
                    "image_url": "https://cdsassets.apple.com/hero.png",
                    "page_url": "https://www.apple.com/iphone-17-pro/specs/",
                    "title": "iPhone 17 Pro - 技术规格",
                    "site_name": "apple.com",
                },
                {
                    "id": image_result_id("https://img.example/b.webp"),
                    "image_url": "https://img.example/b.webp",
                    "page_url": "",
                    "title": "无来源",
                    "site_name": "",
                },
            ],
        )
        self.assertRegex(result.data["images"][0]["id"], r"^img_[0-9a-f]{8}$")

    async def test_fallback_provider_results_are_not_used_as_images(self):
        # Brave 的图片解析返回网页地址，Tavily 不支持图片；回退到它们时不能当图片展示。
        result, _ = await self._execute({"result_provider": "brave", "results": FIRECRAWL_RESULTS})

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.data["images"], [])
        self.assertIsNone(ImageSearchHandler().build_content_block(result, "blk_1", "log_1"))
        self.assertIn("No usable images", ImageSearchHandler().format_llm_context(result))

    async def test_upstream_failure_is_reported_without_images(self):
        with patch(
            "app.services.tool_handlers.image_search.search_images",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await ImageSearchHandler().execute({"query": "猫", "count": 50})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "image_search_unavailable")
        self.assertIn("do not invent image links", ImageSearchHandler().format_llm_context(result))

    async def test_empty_query_fails_without_calling_search(self):
        result, search = await self._execute({"result_provider": "firecrawl", "results": []}, args={"query": "  "})

        self.assertEqual(result.status, "failed")
        search.assert_not_awaited()

    async def test_count_is_bounded(self):
        _, search = await self._execute(
            {"result_provider": "firecrawl", "results": []}, args={"query": "猫", "count": 50}
        )

        search.assert_awaited_once_with("猫", count=10)

    async def test_content_block_and_model_context_expose_ids_not_urls(self):
        handler = ImageSearchHandler()
        result, _ = await self._execute({"result_provider": "firecrawl", "results": FIRECRAWL_RESULTS})

        block = handler.build_content_block(result, "blk_1", "log_1")
        self.assertEqual(block.type, "image_results")
        self.assertEqual(block.schema_version, 1)
        self.assertEqual(block.tool_call_log_id, "log_1")
        self.assertEqual(
            [image.image_url for image in block.images], [image["image_url"] for image in result.data["images"]]
        )
        self.assertIn(("image_results", 1), CONTENT_BLOCK_REGISTRY)

        context = handler.format_llm_context(result)
        self.assertIn(f"id={result.data['images'][0]['id']}", context)
        self.assertIn("source: apple.com", context)
        self.assertNotIn("cdsassets.apple.com", context)

    def test_registered_as_builtin_handler(self):
        self.assertIsInstance(get_handler("image_search"), ImageSearchHandler)
