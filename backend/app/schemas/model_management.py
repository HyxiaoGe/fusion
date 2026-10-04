from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    selectable: bool
    reason: str = Field(min_length=1, max_length=300)
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("model_id", "reason")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized
