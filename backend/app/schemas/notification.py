"""站内通知的分页、已读与精确结果定位协议。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_NOTIFICATION_REVISION = 2**63 - 1
NotificationKind = Literal[
    "run_completed", "run_failed", "run_limit_reached", "run_incomplete", "run_interrupted", "changelog_published"
]
NotificationBusinessType = Literal["ai_conversation", "changelog"]
NotificationFilter = Literal["all", "unread"]


class NotificationTarget(BaseModel):
    type: Literal["conversation"] = "conversation"
    conversation_id: str
    message_id: str
    run_id: str


class ChangelogNotificationTarget(BaseModel):
    type: Literal["changelog"] = "changelog"
    changelog_id: str


class NotificationItem(BaseModel):
    id: str
    business_type: NotificationBusinessType
    kind: NotificationKind
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None
    created_revision: int
    target: Annotated[NotificationTarget | ChangelogNotificationTarget, Field(discriminator="type")]


class NotificationReadResult(BaseModel):
    updated_count: int
    unread_count: int
    unread_conversation_ids: list[str]
    revision: int


class NotificationPage(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    unread_conversation_ids: list[str]
    revision: int
    next_cursor: str | None


class NotificationReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=100)


class NotificationReadAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    through_revision: int = Field(ge=0, le=MAX_NOTIFICATION_REVISION, strict=True)


class NotificationResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)


class NotificationReadResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=128)
    results: list[NotificationResultReference] = Field(min_length=1, max_length=100)
