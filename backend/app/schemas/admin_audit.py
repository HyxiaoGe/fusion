"""管理员审计中心 API 协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SafeCode = Annotated[str, Field(pattern=r"^[a-z0-9:_-]{1,80}$")]
SafeCount = Annotated[int, Field(ge=0, le=1_000_000_000)]
SafeTokenTotal = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]


class AdminAuditUserSummary(BaseModel):
    """审计事件目标用户的当前可读身份摘要。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    nickname: str | None = None


class AdminAuditAdminSnapshot(BaseModel):
    """审计发生时管理员身份的严格快照投影。"""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)


class AdminAuditSearchSummary(BaseModel):
    """搜索条件只保留是否存在与长度，不返回原文。"""

    model_config = ConfigDict(extra="forbid")

    present: bool
    length: SafeCount


class AdminAuditEventMetadata(BaseModel):
    """审计事件允许下发的有限元数据。"""

    model_config = ConfigDict(extra="forbid")

    page: int | None = Field(default=None, ge=1, le=1_000_000_000)
    page_size: int | None = Field(default=None, ge=1, le=100)
    environment: SafeCode | None = None
    schema_version: int | None = Field(default=None, ge=1, le=1_000_000)
    status: SafeCode | None = None
    has_tools: bool | None = None
    has_files: bool | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    model_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")
    source: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")
    q: AdminAuditSearchSummary | None = None
    query: AdminAuditSearchSummary | None = None


class AdminAuditEventItem(BaseModel):
    """管理员访问审计事件的安全返回协议。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    admin_user_id: str
    admin_snapshot: AdminAuditAdminSnapshot
    action: str
    resource_type: str
    resource_id: str | None = None
    target_user_id: str | None = None
    target_user: AdminAuditUserSummary | None = None
    request_id: str
    reason: str | None = None
    metadata: AdminAuditEventMetadata
    created_at: datetime


class AdminModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imageGen: bool = False
    deepThinking: bool = False
    fileSupport: bool = False
    functionCalling: bool = False
    agentTools: bool = False
    searchCapable: bool = False
    vision: bool = False
    webSearch: bool = False


class AdminModelHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "unhealthy", "unknown"]
    error: str | None = Field(default=None, max_length=300)
    checked_at: float | None = Field(default=None, ge=0)


class AdminModelMetricScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: Literal["current_conversation_model"] = "current_conversation_model"
    assistant_messages_and_tokens: Literal["persisted_assistant_messages"] = "persisted_assistant_messages"
    agent_runs: Literal["persisted_agent_sessions"] = "persisted_agent_sessions"
    last_used_at: Literal["persisted_conversation_message_or_agent_activity"] = (
        "persisted_conversation_message_or_agent_activity"
    )


class AdminModelOperationsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    catalog_status: Literal["active", "historical", "unknown"]
    catalog_availability: Literal["available", "degraded"]
    catalog_source: Literal["litellm_model_info"] | None = None
    provider: str | None = Field(default=None, max_length=100, pattern=r"^[a-z0-9._:-]+$")
    provider_display: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=1000)
    knowledge_cutoff: str | None = Field(default=None, max_length=100)
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: AdminModelCapabilities = Field(default_factory=AdminModelCapabilities)
    health: AdminModelHealth = Field(default_factory=lambda: AdminModelHealth(status="unknown"))
    cost_tier: str | None = Field(default=None, max_length=30)
    recommended_for: list[str] = Field(default_factory=list, max_length=50)
    conversation_count: SafeCount = 0
    user_count: SafeCount = 0
    assistant_message_count: SafeCount = 0
    input_tokens: SafeTokenTotal = 0
    output_tokens: SafeTokenTotal = 0
    last_used_at: datetime | None = None
    agent_run_count: SafeCount = 0
    agent_error_count: SafeCount = 0
    metric_scope: AdminModelMetricScope = Field(default_factory=AdminModelMetricScope)
