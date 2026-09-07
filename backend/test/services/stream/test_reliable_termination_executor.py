"""真实 executor 的并发清理、异常身份与结果顺序回归。"""

import asyncio
import gc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.stream.tool_executor import ToolExecutionBatchRequest, execute_tool_batch
from app.services.stream_state_service import StreamOwnershipLostError
from app.services.tool_handlers.base import ToolResult


class ControlledHandler:
    supports_automatic_retry = False

    def __init__(self, name, operation):
        self.tool_name = name
        self.operation = operation

    async def execute(self, args):
        await self.operation()
        return ToolResult(status="success")

    def _build_result_summary(self, result):
        return {"kind": "tool"}

    async def log(self, **kwargs):
        pass


def _request(operations, on_attempt_completed=None):
    emitter = SimpleNamespace(
        **{
            name: AsyncMock()
            for name in (
                "tool_call_started",
                "tool_attempt_started",
                "tool_attempt_completed",
                "tool_call_completed",
                "tool_result_digest",
                "evidence_item_upserted",
            )
        }
    )
    if on_attempt_completed is not None:
        emitter.tool_attempt_completed.side_effect = on_attempt_completed
    return ToolExecutionBatchRequest(
        conversation_id="conv",
        user_id="user",
        model_id="model",
        provider="test",
        emitter=emitter,
        tool_handlers={name: ControlledHandler(name, operation) for name, operation in operations.items()},
    )


def _calls(*names):
    return [{"id": name, "name": name, "arguments": "{}"} for name in names]


@pytest.mark.parametrize("error_type", [StreamOwnershipLostError, asyncio.CancelledError])
@pytest.mark.parametrize("cancel_during_cleanup", [False, True])
def test_batch_waits_for_sibling_cleanup_before_raising_original_error(error_type, cancel_during_cleanup):
    asyncio.run(_batch_waits_for_sibling_cleanup_before_raising_original_error(error_type, cancel_during_cleanup))


async def _batch_waits_for_sibling_cleanup_before_raising_original_error(error_type, cancel_during_cleanup):
    started, cleanup_started, release_cleanup, cleaned = (asyncio.Event() for _ in range(4))
    original = error_type("首个控制流异常")
    owned_tasks = set()

    async def slow():
        owned_tasks.add(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleaned.set()

    async def finish_attempt(**kwargs):
        if kwargs["tool_call_id"] == "fast":
            raise original
        # 兄弟任务收尾异常不得替换首个控制流异常。
        raise StreamOwnershipLostError("兄弟任务的收尾异常")

    request = _request({"fast": started.wait, "slow": slow}, finish_attempt)
    batch = asyncio.create_task(execute_tool_batch(request, _calls("fast", "slow")))
    waiter = asyncio.create_task(cleanup_started.wait())
    try:
        done, _ = await asyncio.wait({batch, waiter}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        assert cleanup_started.is_set(), "批次异常退出前必须取消兄弟工具"
        assert batch not in done, "异步清理结束前批次不得退出"
        if cancel_during_cleanup:
            batch.cancel("清理期间再次取消")
            await asyncio.sleep(0)
        release_cleanup.set()
        with pytest.raises(error_type) as raised:
            await batch
        assert raised.value is original
        assert cleaned.is_set()
    finally:
        release_cleanup.set()
        batch.cancel()
        # 失败基线也要回收本测试的工具任务，避免污染后续用例。
        remaining = [waiter, *owned_tasks]
        for task in remaining:
            task.cancel()
        await asyncio.gather(batch, *remaining, return_exceptions=True)


def test_parent_cancel_waits_for_tool_cleanup():
    asyncio.run(_parent_cancel_waits_for_tool_cleanup())


async def _parent_cancel_waits_for_tool_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    batch = asyncio.create_task(execute_tool_batch(_request({"slow": slow}), _calls("slow")))
    await started.wait()
    batch.cancel("用户取消")
    with pytest.raises(asyncio.CancelledError):
        await batch
    assert cleaned.is_set()


def test_parent_cancel_waits_for_slow_cleanup_after_fast_sibling_exits():
    asyncio.run(_parent_cancel_waits_for_slow_cleanup_after_fast_sibling_exits())


async def _parent_cancel_waits_for_slow_cleanup_after_fast_sibling_exits():
    fast_started, slow_started, fast_cleaned = (asyncio.Event() for _ in range(3))
    cleanup_started, release_cleanup, cleaned = (asyncio.Event() for _ in range(3))
    owned_tasks = set()

    async def fast():
        owned_tasks.add(asyncio.current_task())
        fast_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            fast_cleaned.set()

    async def slow():
        owned_tasks.add(asyncio.current_task())
        slow_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleaned.set()

    batch = asyncio.create_task(execute_tool_batch(_request({"fast": fast, "slow": slow}), _calls("fast", "slow")))
    try:
        await asyncio.wait_for(asyncio.gather(fast_started.wait(), slow_started.wait()), timeout=1)
        batch.cancel("用户取消")
        await asyncio.wait_for(asyncio.gather(fast_cleaned.wait(), cleanup_started.wait()), timeout=1)
        done, _ = await asyncio.wait({batch}, timeout=0.05)
        assert not done, "慢工具清理闸门打开前，批次不能因快工具先退出而返回"
        assert not cleaned.is_set()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await batch
        assert raised.value.args == ("用户取消",)
        assert cleaned.is_set()
    finally:
        release_cleanup.set()
        batch.cancel()
        await asyncio.gather(batch, *owned_tasks, return_exceptions=True)


def test_repeated_parent_cancel_waits_for_single_tool_cleanup_and_keeps_first_cancel():
    unhandled = []
    asyncio.run(_repeated_parent_cancel_waits_for_single_tool_cleanup(unhandled))
    gc.collect()
    assert not unhandled, "聚合 future 与工具任务异常必须全部消费"


async def _repeated_parent_cancel_waits_for_single_tool_cleanup(unhandled):
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unhandled.append(context))
    started, cleanup_started, release_cleanup, cleaned = (asyncio.Event() for _ in range(4))
    owned_tasks = set()

    async def slow():
        owned_tasks.add(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleaned.set()

    batch = asyncio.create_task(execute_tool_batch(_request({"slow": slow}), _calls("slow")))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        batch.cancel("第一次取消")
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        await asyncio.sleep(0)
        batch.cancel("第二次取消")
        done, _ = await asyncio.wait({batch}, timeout=0.05)
        assert not done, "第二次父取消不得让批次在唯一工具清理完成前退出"
        assert not cleaned.is_set()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await batch
        assert raised.value.args == ("第一次取消",)
        assert cleaned.is_set()
    finally:
        release_cleanup.set()
        batch.cancel()
        await asyncio.gather(batch, *owned_tasks, return_exceptions=True)


def test_normal_parallel_batch_returns_input_order():
    asyncio.run(_normal_parallel_batch_returns_input_order())


async def _normal_parallel_batch_returns_input_order():
    slow_started, fast_finished = asyncio.Event(), asyncio.Event()
    completed = []

    async def slow():
        slow_started.set()
        await fast_finished.wait()
        completed.append("slow")

    async def fast():
        await slow_started.wait()
        completed.append("fast")
        fast_finished.set()

    records = await execute_tool_batch(_request({"slow": slow, "fast": fast}), _calls("slow", "fast"))
    assert completed == ["fast", "slow"]
    assert [record.tool_name for record in records] == ["slow", "fast"]
    assert all(record.result.status == "success" for record in records)
