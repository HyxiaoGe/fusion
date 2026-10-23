import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.chat import SearchSource, VideoResultsBlock
from app.schemas.content_block_registry import CONTENT_BLOCK_REGISTRY
from app.services.agent.events import ContentBlockUpserted
from app.services.tool_handlers import get_handler
from app.services.tool_handlers.video_search import (
    VIDEO_SITE_DOMAINS,
    VideoSearchHandler,
    parse_video_page,
    video_result_id,
)


def _source(url: str, title: str = "", description: str = "") -> SearchSource:
    return SearchSource(title=title, url=url, description=description)


SEARCH_RESULTS = [
    _source(
        "https://www.bilibili.com/video/BV1QhDfYWEZw/", "厦门鼓浪屿，登上最高的日光岩 - Bilibili", "视频播放量39999"
    ),
    # 搜索页、话题页、抖音聚合页不是单个视频，不能嵌入。
    _source("https://search.bilibili.com/all?keyword=%E9%BC%93", "鼓浪屿旅游攻略-哔哩哔哩"),
    _source("https://m.bilibili.com/topic-detail?topic_id=1038963", "鼓浪屿风景区| bilibili话题"),
    _source("https://www.douyin.com/shipin/7263593690277496891", "外国人去鼓浪屿怎么买票 - 抖音"),
    _source(
        "https://m.v.qq.com/x/m/play?cid=&vid=g0530dqs1i6",
        "鼓浪屿旅游含船票攻略 - 腾讯视频",
        "【腾讯视频】厦门旅游攻略",
    ),
    _source(
        "https://www.douyin.com/video/7690461347934988130", "想要去鼓浪屿旅游的看过来#鼓浪屿", "  探了个店\n发布在抖音 "
    ),
    # 同一个视频的移动版地址只保留一次。
    _source("https://m.bilibili.com/video/BV1QhDfYWEZw", "重复"),
]


class ParseVideoPageTests(unittest.TestCase):
    def test_parses_single_video_pages(self):
        cases = {
            "https://www.bilibili.com/video/BV1QhDfYWEZw/": ("bilibili", "BV1QhDfYWEZw"),
            "https://bilibili.com/video/BV1ZW411a7Cy": ("bilibili", "BV1ZW411a7Cy"),
            "https://www.douyin.com/video/7690461347934988130": ("douyin", "7690461347934988130"),
            "https://m.v.qq.com/x/m/play?cid=&vid=g0530dqs1i6": ("tencent", "g0530dqs1i6"),
            "https://v.qq.com/x/page/o3368uw3ut4.html": ("tencent", "o3368uw3ut4"),
            "https://v.qq.com/x/cover/mzc00200abcdefg/o3368uw3ut4.html": ("tencent", "o3368uw3ut4"),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(parse_video_page(url), expected)

    def test_rejects_non_video_pages(self):
        for url in [
            "https://search.bilibili.com/all?keyword=x",
            "https://www.bilibili.com/video/BV1QhDfYWEZw/extra",
            "https://www.douyin.com/shipin/7263593690277496891",
            "https://v.qq.com/x/cover/mzc00200abcdefg.html",
            "https://m.v.qq.com/x/m/play?vid=<script>",
            "https://evil.example/video/BV1QhDfYWEZw",
            "javascript:alert(1)",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(parse_video_page(url))


class VideoSearchHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def _execute(self, results, args=None):
        with patch(
            "app.services.tool_handlers.video_search.search_web",
            new=AsyncMock(return_value=results),
        ) as search:
            result = await VideoSearchHandler().execute(args or {"query": "鼓浪屿 旅游"})
        return result, search

    async def test_keeps_only_embeddable_videos_with_stable_ids(self):
        result, search = await self._execute(SEARCH_RESULTS)

        search.assert_awaited_once_with("鼓浪屿 旅游", count=10, domains=VIDEO_SITE_DOMAINS, scene="video_search")
        self.assertEqual(result.status, "success")
        self.assertEqual(
            [(video["platform"], video["video_id"]) for video in result.data["videos"]],
            [("bilibili", "BV1QhDfYWEZw"), ("tencent", "g0530dqs1i6"), ("douyin", "7690461347934988130")],
        )
        self.assertEqual(result.data["videos"][0]["id"], video_result_id("bilibili", "BV1QhDfYWEZw"))
        self.assertRegex(result.data["videos"][0]["id"], r"^vid_[0-9a-f]{8}$")
        self.assertEqual(result.data["videos"][2]["snippet"], "探了个店 发布在抖音")

    async def test_count_limits_returned_videos(self):
        result, _ = await self._execute(SEARCH_RESULTS, args={"query": "鼓浪屿", "count": 1})

        self.assertEqual(len(result.data["videos"]), 1)

    async def test_no_embeddable_videos_is_degraded(self):
        result, _ = await self._execute(SEARCH_RESULTS[1:4])

        self.assertEqual(result.status, "degraded")
        self.assertIsNone(VideoSearchHandler().build_content_block(result, "blk_1", "log_1"))
        self.assertIn("No embeddable videos", VideoSearchHandler().format_llm_context(result))

    async def test_upstream_failure_is_reported_without_videos(self):
        with patch(
            "app.services.tool_handlers.video_search.search_web",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await VideoSearchHandler().execute({"query": "鼓浪屿"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "video_search_unavailable")
        self.assertIn("do not invent video links", VideoSearchHandler().format_llm_context(result))

    async def test_empty_query_fails_without_calling_search(self):
        result, search = await self._execute([], args={"query": "  "})

        self.assertEqual(result.status, "failed")
        search.assert_not_awaited()

    async def test_content_block_and_model_context_expose_ids_not_urls(self):
        handler = VideoSearchHandler()
        result, _ = await self._execute(SEARCH_RESULTS)

        block = handler.build_content_block(result, "blk_1", "log_1")
        self.assertEqual(block.type, "video_results")
        self.assertEqual(block.schema_version, 1)
        self.assertEqual(block.tool_call_log_id, "log_1")
        self.assertEqual(block.videos[0].platform, "bilibili")
        self.assertEqual(block.videos[0].page_url, "https://www.bilibili.com/video/BV1QhDfYWEZw/")
        self.assertIn(("video_results", 1), CONTENT_BLOCK_REGISTRY)
        # 流式过程中靠 content_block_upserted 送达前端，事件白名单漏了会只在刷新后才显示视频。
        event = ContentBlockUpserted(
            type="content_block_upserted",
            protocol_version=2,
            run_id="run-1",
            sequence=1,
            trace_id="trace-1",
            ts=1.0,
            content_block=block,
        )
        self.assertIsInstance(event.content_block, VideoResultsBlock)

        context = handler.format_llm_context(result)
        self.assertIn(f"id={result.data['videos'][0]['id']}", context)
        self.assertIn("platform: Bilibili", context)
        self.assertNotIn("bilibili.com/video", context)

    def test_registered_as_builtin_handler(self):
        self.assertIsInstance(get_handler("video_search"), VideoSearchHandler)
