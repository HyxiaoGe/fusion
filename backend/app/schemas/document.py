"""交付物文档的 API 与来源契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DocumentSourceKind = Literal["weather", "place", "route", "flight", "train", "itinerary", "web", "url"]


class DocumentSource(BaseModel):
    """系统按本次实际工具调用生成的数据来源条目，不接受模型自报。"""

    model_config = ConfigDict(extra="forbid")

    kind: DocumentSourceKind
    label: str = Field(min_length=1, max_length=200)
    provider: Optional[str] = Field(default=None, max_length=80)
    url: Optional[str] = Field(default=None, max_length=2048)
    fetched_at: Optional[datetime] = None


class DocumentVersionSummaryResponse(BaseModel):
    version: int
    title: str
    change_summary: Optional[str] = None
    char_count: int
    created_at: Optional[datetime] = None


class DocumentDetailResponse(BaseModel):
    id: str
    conversation_id: str
    title: str
    format: Literal["markdown"]
    current_version: int
    versions: list[DocumentVersionSummaryResponse]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentVersionResponse(BaseModel):
    document_id: str
    version: int
    title: str
    format: Literal["markdown"]
    content: str
    change_summary: Optional[str] = None
    sources: list[DocumentSource]
    created_at: Optional[datetime] = None
