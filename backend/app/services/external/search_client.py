"""
搜索服务客户端 — 封装对私有 search-service 的 HTTP 调用
"""

from typing import List

import httpx

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.schemas.chat import SearchSource


async def search_web(
    query: str,
    count: int = 10,
    *,
    domains: list[str] | None = None,
    recency_days: int | None = None,
) -> List[SearchSource]:
    """
    调用 search-service 执行网络搜索。
    返回 SearchSource 列表；失败交由工具 handler 转换为结构化失败，不能冒充空结果。
    """
    try:
        payload = {
            "query": query,
            "type": "web",
            "count": count,
            "freshness": _freshness_from_recency_days(recency_days),
        }
        if domains:
            payload["domain_filters"] = domains

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.SEARCH_SERVICE_URL}/search",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        requested_provider = data.get("requested_provider")
        result_provider = data.get("result_provider") or data.get("provider")
        fallback_used = bool(data.get("fallback_used", False))
        provider_chain = data.get("provider_chain") if isinstance(data.get("provider_chain"), list) else []

        return [
            SearchSource(
                title=r.get("title", ""),
                url=r.get("url", ""),
                description=r.get("description", ""),
                content=r.get("content"),
                favicon=r.get("favicon"),
                published_at=r.get("published_at"),
                site_name=r.get("site_name"),
                requested_provider=requested_provider,
                result_provider=result_provider,
                fallback_used=fallback_used,
                provider_chain=provider_chain,
            )
            for r in data.get("results", [])
        ]
    except Exception as exc:
        # 上游异常可能没有文本或含请求凭据，只记录类型，保留失败给现有 handler 处理。
        logger.warning("搜索服务调用失败: error_type=%s", type(exc).__name__)
        raise


def _freshness_from_recency_days(recency_days: int | None) -> str | None:
    if recency_days is None:
        return None
    if recency_days <= 1:
        return "pd"
    if recency_days <= 7:
        return "pw"
    if recency_days <= 31:
        return "pm"
    return "py"


# 图片结果只有 Firecrawl 的格式经过适配；显式指定，不随 search-service 默认网页 provider 变化。
IMAGE_SEARCH_PROVIDER = "firecrawl"


async def search_images(query: str, count: int = 8) -> dict:
    """调用 search-service 图片搜索，返回原始 results 与实际服务商；失败抛给 handler 转成结构化失败。"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{settings.SEARCH_SERVICE_URL}/search",
                json={"query": query, "type": "image", "count": count, "provider": IMAGE_SEARCH_PROVIDER},
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "result_provider": data.get("result_provider") or data.get("provider") or "",
            "results": data.get("results") if isinstance(data.get("results"), list) else [],
        }
    except Exception as exc:
        logger.warning("图片搜索调用失败: error_type=%s", type(exc).__name__)
        raise
