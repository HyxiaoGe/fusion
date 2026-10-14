from pydantic import BaseModel


class UserMemoryItem(BaseModel):
    id: str
    category: str
    content: str
    source_conversation_id: str | None = None
    updated_at: str


class UserMemoryOverview(BaseModel):
    enabled: bool
    items: list[UserMemoryItem]


class UserMemorySettingsUpdate(BaseModel):
    enabled: bool
