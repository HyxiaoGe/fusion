"""前端后台节拍的聚合同步协议：一次请求完成心跳续期并取回各业务的轻量状态。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SyncSection = Literal["viewing", "activity", "notifications"]


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 对话页处于前台时带上，续期「正在看」标记。
    viewing_conversation_id: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    activity: bool = False
    notifications: bool = False


class SyncActivity(BaseModel):
    streaming: list[str]
    unread: list[str]


class SyncNotifications(BaseModel):
    revision: int


class SyncResponse(BaseModel):
    """各部分独立：未请求为 null；请求了但失败时为 null 且列入 failed，不影响其他部分。"""

    viewing: bool | None = None
    activity: SyncActivity | None = None
    notifications: SyncNotifications | None = None
    failed: list[SyncSection] = []
