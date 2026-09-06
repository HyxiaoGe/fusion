"""分类后的有序系统消息及其冻结模板来源。"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.prompts.prompt_message import PromptMessage
from app.core.prompt_snapshot import PromptBundleSnapshot
from app.utils.prompt_fingerprint import fingerprint_system_messages


@dataclass(frozen=True)
class RunPromptSnapshot:
    bundle_snapshot: PromptBundleSnapshot
    messages: tuple[PromptMessage, ...]
    template_version: str

    def __post_init__(self):
        if not self.messages or any(message.role != "system" or not message.section_id for message in self.messages):
            raise ValueError("Run Prompt 快照必须包含带 section identity 的系统消息")
        if len({message.section_id for message in self.messages}) != len(self.messages):
            raise ValueError("Run Prompt 快照包含重复 section identity")

    @property
    def classifier_prompt(self) -> str:
        return self.bundle_snapshot.classifier_prompt

    @property
    def fingerprint(self) -> str:
        return fingerprint_system_messages(self.messages)

    def resolve(self, name: str) -> tuple[str, dict[str, str | None]]:
        return self.bundle_snapshot.resolve(name)

    def to_storage(self) -> dict:
        """正文表继续使用兼容的 schema；轻量身份已在 Run 创建事务中保存。"""
        return {
            "schema_version": 1,
            "template_version": self.template_version,
            "fingerprint": self.fingerprint,
            "char_count": sum(len(message.content) for message in self.messages),
            "sections": [{"section_id": message.section_id, "content": message.content} for message in self.messages],
        }
