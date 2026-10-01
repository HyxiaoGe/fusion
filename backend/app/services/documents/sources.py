"""从本 Run 已投影的工具结果块生成文档数据来源；只认服务端结构化结果，不接受模型自报。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schemas.chat import (
    FlightResultsBlock,
    ItineraryResultsBlock,
    PlaceResultsBlock,
    RouteResultsBlock,
    SearchBlock,
    TrainResultsBlock,
    UrlBlock,
    WeatherResultsBlock,
)
from app.schemas.document import DocumentSource

_MAX_LABEL_CHARS = 200


def document_sources_from_blocks(blocks: Iterable[Any]) -> list[DocumentSource]:
    sources: list[DocumentSource] = []
    for block in blocks:
        sources.extend(_sources_for_block(block))
    return sources


def _sources_for_block(block: Any) -> list[DocumentSource]:
    if isinstance(block, WeatherResultsBlock):
        return [_source("weather", block.resolved_location, block.provider, fetched_at=block.fetched_at)]
    if isinstance(block, PlaceResultsBlock):
        label = f"{block.near} · {block.query}" if block.near else block.query
        return [_source("place", label, block.provider)]
    if isinstance(block, RouteResultsBlock):
        return [_source("route", f"{block.origin.label} → {block.destination.label}", block.provider)]
    if isinstance(block, FlightResultsBlock):
        label = f"{block.origin} → {block.destination} {block.departure_date}"
        return [_source("flight", label, block.provider, fetched_at=block.observed_at)]
    if isinstance(block, TrainResultsBlock):
        label = f"{block.origin} → {block.destination} {block.departure_date}"
        return [_source("train", label, block.provider, fetched_at=block.observed_at)]
    if isinstance(block, ItineraryResultsBlock):
        label = f"{block.origin} → {block.destination} {block.start_date}"
        return [_source("itinerary", label, block.provider)]
    if isinstance(block, SearchBlock) and block.status != "failed":
        return [
            _source("web", item.title or item.url, block.result_provider, url=item.url)
            for item in block.sources
            if item.url
        ]
    if isinstance(block, UrlBlock) and block.status != "failed":
        return [_source("url", block.title or block.url, None, url=block.url)]
    return []


def _source(kind: str, label: str, provider: str | None, *, url: str | None = None, fetched_at=None) -> DocumentSource:
    return DocumentSource(
        kind=kind,
        label=label.strip()[:_MAX_LABEL_CHARS] or kind,
        provider=provider[:80] if provider else None,
        url=url if url and len(url) <= 2048 else None,
        fetched_at=fetched_at,
    )
