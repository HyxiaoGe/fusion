"""携带内部 section identity 的不可变模型消息。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from app.ai.prompts.section_ids import VISIBLE_RESPONSE_LANGUAGE


@dataclass(frozen=True, eq=False)
class PromptMessage(Mapping[str, Any]):
    """在 provider payload 之外保留稳定段落身份。

    `section_id` 只属于 Fusion 进程内控制面。Mapping 视图和
    `to_provider_dict()` 都只暴露 provider 接受的消息字段。
    """

    role: str
    content: Any
    section_id: str | None = None
    provider_fields: Mapping[str, Any] = field(default_factory=dict, repr=False, kw_only=True)

    def __post_init__(self) -> None:
        if not self.role:
            raise ValueError("消息 role 不能为空")
        if self.role != "system" and self.section_id is not None:
            raise ValueError("非 system 消息不能携带 section_id")
        if self.section_id == "":
            raise ValueError("section_id 不能为空字符串")
        fields = dict(self.provider_fields)
        reserved = {"role", "content", "section_id"}.intersection(fields)
        if reserved:
            raise ValueError(f"provider_fields 包含保留字段: {sorted(reserved)}")
        object.__setattr__(self, "provider_fields", MappingProxyType(fields))

    @classmethod
    def from_provider_dict(
        cls,
        message: Mapping[str, Any],
        *,
        section_id: str | None = None,
    ) -> PromptMessage:
        payload = dict(message)
        role = str(payload.pop("role"))
        content = payload.pop("content", None)
        payload.pop("section_id", None)
        return cls(
            role=role,
            content=content,
            section_id=section_id,
            provider_fields=payload,
        )

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": deepcopy(self.content),
            **deepcopy(dict(self.provider_fields)),
        }

    def with_content(self, content: Any) -> PromptMessage:
        return PromptMessage(
            role=self.role,
            content=content,
            section_id=self.section_id,
            provider_fields=self.provider_fields,
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> PromptMessage:
        """保留内部身份，并为可变的多模态内容生成独立副本。"""

        copied = PromptMessage(
            role=self.role,
            content=deepcopy(self.content, memo),
            section_id=self.section_id,
            provider_fields=deepcopy(dict(self.provider_fields), memo),
        )
        memo[id(self)] = copied
        return copied

    def __getitem__(self, key: str) -> Any:
        if key == "role":
            return self.role
        if key == "content":
            return self.content
        return self.provider_fields[key]

    def __iter__(self) -> Iterator[str]:
        yield "role"
        yield "content"
        yield from self.provider_fields

    def __len__(self) -> int:
        return 2 + len(self.provider_fields)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PromptMessage):
            return (
                self.role == other.role
                and self.content == other.content
                and self.section_id == other.section_id
                and dict(self.provider_fields) == dict(other.provider_fields)
            )
        if isinstance(other, Mapping):
            return self.to_provider_dict() == dict(other)
        return NotImplemented

    __hash__ = None


def ensure_prompt_message(
    message: PromptMessage | Mapping[str, Any],
    *,
    section_id: str | None = None,
) -> PromptMessage:
    """把 provider 风格映射提升为内部消息，不把身份写入映射。"""

    if isinstance(message, PromptMessage):
        if section_id is None or section_id == message.section_id:
            return message
        return PromptMessage(
            role=message.role,
            content=message.content,
            section_id=section_id,
            provider_fields=message.provider_fields,
        )
    return PromptMessage.from_provider_dict(message, section_id=section_id)


def ensure_prompt_messages(
    messages: Sequence[PromptMessage | Mapping[str, Any]],
) -> list[PromptMessage]:
    return [ensure_prompt_message(message) for message in messages]


def to_provider_messages(
    messages: Sequence[PromptMessage | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """在 tokenizer / LiteLLM 外部边界生成不含内部身份的兼容 payload。"""

    projected: list[dict[str, Any]] = []
    for raw_message in messages:
        message = ensure_prompt_message(raw_message)
        payload = message.to_provider_dict()
        if (
            message.section_id == VISIBLE_RESPONSE_LANGUAGE
            and projected
            and projected[-1].get("role") == "system"
            and isinstance(projected[-1].get("content"), str)
        ):
            projected[-1]["content"] = f"{projected[-1]['content'].rstrip()}\n\n{message.content}"
            continue
        projected.append(payload)
    return projected
