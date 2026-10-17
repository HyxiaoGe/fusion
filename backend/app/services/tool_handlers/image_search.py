"""image_search：给回答配图。

模型只拿到每张图的 id、标题与来源网页，看不到图片本身；正文用 ``![说明](id)`` 引用，前端只按本条回复
image_results 块里的 id 渲染真实地址并标注来源，模型无法让页面加载它自己编写的图片链接。
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.ai.tools import DEFAULT_IMAGE_COUNT, IMAGE_SEARCH_TOOL_NAME, MAX_IMAGE_COUNT, MAX_IMAGE_QUERY_CHARS
from app.schemas.chat import ImageResultItem, ImageResultsBlock
from app.services.external.search_client import search_images
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

# 只有这些服务商的图片结果里 url 是图片本身：Brave 的解析返回的是网页地址，Tavily 不支持图片搜索。
# search-service 主服务商失败时会回退到别家，回退结果不能当图片用。
IMAGE_RESULT_PROVIDERS = frozenset({"firecrawl"})


def image_result_id(image_url: str) -> str:
    """按图片地址生成稳定 id；同一 run 多次搜索也不会撞号，且不依赖调用顺序。"""
    return "img_" + hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:8]


def _https_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    # 只放 https：主站是 https，http 图片会被浏览器拦截或强制升级后加载失败。
    return url if parts.scheme == "https" and parts.netloc else ""


def _page_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in {"http", "https"} and parts.netloc else ""


def _site_name(page_url: str) -> str:
    host = urlsplit(page_url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _normalize_images(results: list[Any], *, limit: int) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        image_url = _https_url(item.get("url"))
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        # search-service 图片结果把来源网页放在 description 字段。
        page_url = _page_url(item.get("description"))
        images.append(
            {
                "id": image_result_id(image_url),
                "image_url": image_url[:2_000],
                "page_url": page_url[:2_000],
                "title": " ".join(str(item.get("title") or "").split())[:300],
                "site_name": _site_name(page_url)[:120],
            }
        )
        if len(images) >= limit:
            break
    return images


def _requested_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_IMAGE_COUNT
    return max(1, min(value, MAX_IMAGE_COUNT))


class ImageSearchHandler(BaseToolHandler):
    @property
    def tool_name(self) -> str:
        return IMAGE_SEARCH_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "image_search"

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        query = " ".join(str(input_params.get("query") or "").split()).lower()
        return f"{IMAGE_SEARCH_TOOL_NAME}:{query}:{_requested_count(input_params.get('count'))}" if query else None

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        query = " ".join(str(args.get("query") or "").split())[:MAX_IMAGE_QUERY_CHARS]
        count = _requested_count(args.get("count"))
        data: dict[str, Any] = {"query": query, "images": []}
        if not query:
            return ToolResult(status="failed", data={**data, "error_code": "empty_query"}, error_message="query 为空")
        try:
            response = await search_images(query, count=count)
        except Exception:  # noqa: BLE001 — 上游异常只记类型，交给模型如实说明
            return ToolResult(
                status="failed",
                data={**data, "error_code": "image_search_unavailable"},
                error_message="图片搜索暂时不可用",
            )
        provider = str(response.get("result_provider") or "")
        results = (response.get("results") or []) if provider in IMAGE_RESULT_PROVIDERS else []
        images = _normalize_images(results, limit=count)
        data = {**data, "images": images, "result_provider": provider}
        return ToolResult(status="success" if images else "degraded", data=data)

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> ImageResultsBlock | None:
        images = (result.data or {}).get("images") or []
        if result.status == "failed" or not images:
            return None
        return ImageResultsBlock(
            type="image_results",
            id=block_id,
            schema_version=1,
            query=str(result.data.get("query") or "")[:400],
            images=[ImageResultItem(**image) for image in images],
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status == "failed":
            return render_runtime_prompt("ai_tools.image_search_failed", reason=result.error_message or "unknown")
        images = data.get("images") or []
        if not images:
            return render_runtime_prompt("ai_tools.image_search_empty", query=data.get("query", ""))
        lines = [
            f"- id={image['id']} | title: {image['title'] or '(none)'} | source: {image['site_name'] or 'unknown'}"
            for image in images
        ]
        return render_runtime_prompt("ai_tools.image_search_context", images="\n".join(lines))

    def _build_result_summary(self, result: ToolResult) -> dict:
        images = (result.data or {}).get("images") or []
        return {"kind": "image_search", "count": len(images), "truncated": False}
