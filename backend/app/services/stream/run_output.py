"""Run 输出出口：模型正文、思考和服务端补写的回答写到哪里，由创建 run 的一方决定。

loop 只调用出口，不直接知道 Redis 对话流。聊天请求用 ConversationStreamOutput 写进
对话的实时流；不挂在聊天消息上的运行（离线评测、子代理）可以换成别的实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.services import stream_state_service


class RunOutput(Protocol):
    async def append(self, chunk_type: str, content: str, block_id: str, **extras: Any) -> str | None:
        """写入一个输出片段；extras 是 run_id / step_id 等关联字段。"""

    async def owns_stream(self) -> bool:
        """本次运行是否仍有写入权；False 表示已被新请求取代，应停止生成。"""


@dataclass(frozen=True)
class ConversationStreamOutput:
    """写进对话的 Redis 实时流，写入权由对话流锁按 task_id 判定。"""

    conversation_id: str
    task_id: str

    async def append(self, chunk_type: str, content: str, block_id: str, **extras: Any) -> str | None:
        return await stream_state_service.append_chunk(
            self.conversation_id,
            chunk_type,
            content,
            block_id,
            task_id=self.task_id,
            **extras,
        )

    async def owns_stream(self) -> bool:
        return await stream_state_service.check_lock_owner(self.conversation_id, self.task_id)
