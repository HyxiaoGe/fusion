"""video_search：在回答里嵌入视频。

走 search-service 网页搜索并限定 B 站、抖音、腾讯视频站点，只保留能解析出视频 id 的单个视频页
（搜索页、话题页、抖音聚合页都丢掉）。模型只拿到 id、平台、标题和摘要，看不到视频；正文用
``![说明](id)`` 引用，前端按本条回复 video_results 块里的平台与视频 id 拼官方播放器地址嵌入。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.ai.tools import DEFAULT_VIDEO_COUNT, MAX_VIDEO_COUNT, MAX_VIDEO_QUERY_CHARS, VIDEO_SEARCH_TOOL_NAME
from app.schemas.chat import VideoResultItem, VideoResultsBlock
from app.services.external.search_client import search_web
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

VIDEO_SITE_DOMAINS = ["bilibili.com", "douyin.com", "v.qq.com"]
# 每次多取一些：限定站点后约一半结果是搜索页、话题页这类不能嵌入的页面。
VIDEO_SEARCH_RESULT_COUNT = 10

PLATFORM_LABELS = {"bilibili": "Bilibili", "douyin": "Douyin", "tencent": "Tencent Video"}

_BILIBILI_PATH_RE = re.compile(r"^/video/(BV[0-9A-Za-z]{10})/?$")
_DOUYIN_PATH_RE = re.compile(r"^/video/(\d{15,20})/?$")
_TENCENT_VID_RE = re.compile(r"^[0-9a-z]{11}$")
_TENCENT_PAGE_RE = re.compile(r"^/x/(?:page|cover/[0-9a-z]+)/([0-9a-z]{11})\.html$")


def video_result_id(platform: str, video_id: str) -> str:
    """按平台与视频 id 生成稳定 id；同一 run 多次搜索也不会撞号。"""
    return "vid_" + hashlib.sha256(f"{platform}:{video_id}".encode("utf-8")).hexdigest()[:8]


def parse_video_page(url: str) -> tuple[str, str] | None:
    """从视频页地址解析 (平台, 视频 id)；不是单个视频页返回 None。"""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        match = _BILIBILI_PATH_RE.match(parts.path)
        return ("bilibili", match.group(1)) if match else None
    if host in {"douyin.com", "www.douyin.com"}:
        match = _DOUYIN_PATH_RE.match(parts.path)
        return ("douyin", match.group(1)) if match else None
    if host in {"v.qq.com", "m.v.qq.com"}:
        if parts.path == "/x/m/play":
            vid = (parse_qs(parts.query).get("vid") or [""])[0]
            return ("tencent", vid) if _TENCENT_VID_RE.match(vid) else None
        match = _TENCENT_PAGE_RE.match(parts.path)
        return ("tencent", match.group(1)) if match else None
    return None


def _clean_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalize_videos(results: list[Any], *, limit: int) -> list[dict[str, str]]:
    videos: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in results:
        url = str(getattr(item, "url", "") or "")
        parsed = parse_video_page(url)
        if parsed is None:
            continue
        platform, video_id = parsed
        result_id = video_result_id(platform, video_id)
        if result_id in seen:
            continue
        seen.add(result_id)
        videos.append(
            {
                "id": result_id,
                "platform": platform,
                "video_id": video_id,
                "page_url": url.strip()[:2_000],
                "title": _clean_text(getattr(item, "title", ""), 300),
                "snippet": _clean_text(getattr(item, "description", ""), 200),
            }
        )
        if len(videos) >= limit:
            break
    return videos


def _requested_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_VIDEO_COUNT
    return max(1, min(value, MAX_VIDEO_COUNT))


class VideoSearchHandler(BaseToolHandler):
    @property
    def tool_name(self) -> str:
        return VIDEO_SEARCH_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "video_search"

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        query = " ".join(str(input_params.get("query") or "").split()).lower()
        return f"{VIDEO_SEARCH_TOOL_NAME}:{query}:{_requested_count(input_params.get('count'))}" if query else None

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        query = " ".join(str(args.get("query") or "").split())[:MAX_VIDEO_QUERY_CHARS]
        count = _requested_count(args.get("count"))
        data: dict[str, Any] = {"query": query, "videos": []}
        if not query:
            return ToolResult(status="failed", data={**data, "error_code": "empty_query"}, error_message="query 为空")
        try:
            results = await search_web(query, count=VIDEO_SEARCH_RESULT_COUNT, domains=VIDEO_SITE_DOMAINS)
        except Exception:  # noqa: BLE001 — 上游异常只记类型，交给模型如实说明
            return ToolResult(
                status="failed",
                data={**data, "error_code": "video_search_unavailable"},
                error_message="视频搜索暂时不可用",
            )
        # 主服务商失败回退到不支持站点限定的服务商时，结果里不是视频页的会在解析时被丢掉。
        videos = _normalize_videos(results, limit=count)
        data = {**data, "videos": videos}
        return ToolResult(status="success" if videos else "degraded", data=data)

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> VideoResultsBlock | None:
        videos = (result.data or {}).get("videos") or []
        if result.status == "failed" or not videos:
            return None
        return VideoResultsBlock(
            type="video_results",
            id=block_id,
            schema_version=1,
            query=str(result.data.get("query") or "")[:400],
            videos=[
                VideoResultItem(
                    id=video["id"],
                    platform=video["platform"],
                    video_id=video["video_id"],
                    page_url=video["page_url"],
                    title=video["title"],
                )
                for video in videos
            ],
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status == "failed":
            return render_runtime_prompt("ai_tools.video_search_failed", reason=result.error_message or "unknown")
        videos = data.get("videos") or []
        if not videos:
            return render_runtime_prompt("ai_tools.video_search_empty", query=data.get("query", ""))
        lines = [
            f"- id={video['id']} | platform: {PLATFORM_LABELS[video['platform']]} | "
            f"title: {video['title'] or '(none)'} | snippet: {video['snippet'] or '(none)'}"
            for video in videos
        ]
        return render_runtime_prompt("ai_tools.video_search_context", videos="\n".join(lines))

    def _build_result_summary(self, result: ToolResult) -> dict:
        videos = (result.data or {}).get("videos") or []
        return {"kind": "video_search", "count": len(videos), "truncated": False}
