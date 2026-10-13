"""首个输出超时：模型迟迟不吐字时放弃本次请求，由 agent loop 换备用模型。

首个输出按思考、正文、工具调用任一先到算，推理模型思考久不会被误判；
计时从发出请求开始，覆盖建连、服务商排队与首个 chunk。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.ai.llm_round_observability import output_delta_kinds

# 近 7 天 mimo-flash 首字中位约 2.7s，超过 10s 的约 11%，多为服务商偶发卡顿。
FIRST_OUTPUT_TIMEOUT_S = 10.0
FALLBACK_MODEL_ID = "deepseek-chat"


class FirstOutputTimeoutError(TimeoutError):
    def __init__(self, model_id: str | None, timeout_s: float):
        super().__init__(f"model {model_id} produced no output within {timeout_s:g}s")
        self.model_id = model_id
        self.timeout_s = timeout_s


def _is_output_chunk(chunk: Any) -> bool:
    if output_delta_kinds(chunk):
        return True
    choices = getattr(chunk, "choices", None) or []
    return bool(choices) and getattr(choices[0], "finish_reason", None) is not None


class _ReplayIterator:
    def __init__(self, buffered: list[Any], iterator: AsyncIterator[Any] | None):
        self._buffered = buffered
        self._iterator = iterator

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        if self._buffered:
            return self._buffered.pop(0)
        if self._iterator is None:
            raise StopAsyncIteration
        return await self._iterator.__anext__()


async def _close_quietly(response: Any) -> None:
    close = getattr(response, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        pass


async def await_first_output(
    open_response: Callable[[], Awaitable[Any]],
    *,
    wrap_response: Callable[[Any], Any],
    timeout_s: float,
    model_id: str | None,
) -> Any:
    """在 timeout_s 内拿到首个输出 chunk，返回可完整重放的流；超时关闭请求并抛错。"""

    raw_response: Any = None
    buffered: list[Any] = []
    iterator: AsyncIterator[Any] | None = None

    async def _prefetch() -> None:
        nonlocal raw_response, iterator
        raw_response = await open_response()
        response = wrap_response(raw_response)
        if not hasattr(response, "__aiter__"):
            raise TypeError("streaming response expected")
        iterator = response.__aiter__()
        while True:
            try:
                chunk = await iterator.__anext__()
            except StopAsyncIteration:
                iterator = None
                return
            buffered.append(chunk)
            if _is_output_chunk(chunk):
                return

    try:
        await asyncio.wait_for(_prefetch(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        await _close_quietly(raw_response)
        raise FirstOutputTimeoutError(model_id, timeout_s) from exc
    return _ReplayIterator(buffered, iterator)
