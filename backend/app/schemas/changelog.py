"""发布后不可原位修改的更新日志协议。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChangelogPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("version", "title", "summary", mode="before")
    @classmethod
    def strip_metadata(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def preserve_markdown(cls, value: str) -> str:
        # 仅检查空白，缩进、行尾与末尾换行都是 Markdown 正文的一部分。
        if not value.strip():
            raise ValueError("更新日志正文不能为空白")
        return value


class ChangelogSummary(BaseModel):
    id: str
    version: str
    title: str
    summary: str
    published_at: datetime


class ChangelogDetail(ChangelogSummary):
    content: str
    notification_id: str | None


class ChangelogPage(BaseModel):
    # 更新日志页按时间线完整展开，列表项直接带正文和当前用户的通知 id。
    items: list[ChangelogDetail]
    next_cursor: str | None
