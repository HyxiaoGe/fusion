"""实际工具反馈及裁剪可见范围的回归。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ToolCallLog
from app.services.agent.trajectory_payload import build_trajectory_payload
from app.services.chat.context_manager import prepare_context
from app.services.stream import tool_round
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.tool_handlers.base import ToolResult
from app.services.trajectory_query_service import TrajectoryQueryService


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _record():
    handler = SimpleNamespace(
        format_llm_context=lambda result: "已编号的实际反馈 [7]，token=secret-value",
        build_content_block=lambda *args: None,
    )
    return ToolExecutionRecord(
        tool_call={"id": "call-1", "name": "weather", "arguments": {}},
        result=ToolResult(status="failed", data={"raw": "原始业务载荷"}),
        handler=handler,
        block_id="block-1",
        log_id="log-1",
    )


def _request(record):
    return SimpleNamespace(
        tool_calls=[record.tool_call],
        messages=[],
        content_blocks=[],
        reasoning_buf="",
        should_use_reasoning=False,
        announced_tool_names=frozenset({"weather", "web_search"}),
        run_id="run-1",
        conversation_id="conv-1",
        user_id="user-1",
        step_number=1,
        originating_llm_round_id="origin-round",
    )


@pytest.mark.anyio
async def test_formatted_feedback_survives_delayed_log_and_reload(tmp_path):
    record = _record()
    request = _request(record)
    engine = create_engine(f"sqlite:///{tmp_path / 'observation.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    release_log = asyncio.Event()
    committed = asyncio.Event()
    loop = asyncio.get_running_loop()
    attach = tool_round.attach_tool_observation

    def attach_and_confirm(**kwargs):
        attach(**kwargs)
        loop.call_soon_threadsafe(committed.set)

    async def delayed_log():
        await release_log.wait()
        with session_factory() as db:
            db.add(
                ToolCallLog(
                    id="log-1",
                    conversation_id="conv-1",
                    user_id="user-1",
                    tool_name="weather",
                    status="failed",
                    trace_id="run-1",
                    tool_call_id="call-1",
                    model_id="model",
                    provider="test",
                    extra_metadata={"business_metadata": "保留"},
                    output_data={"raw": "原始业务载荷"},
                )
            )
            db.commit()

    record.result.trajectory_log_task = asyncio.create_task(delayed_log())
    observations = tool_round.append_tool_round_messages_with_plan(request, [record], source_plan=None)
    assert observations["call-1"] == request.messages[-1]["content"]
    with (
        patch("app.services.agent_logger.SessionLocal", session_factory),
        patch.object(tool_round, "attach_tool_observation", attach_and_confirm),
    ):
        saving = asyncio.create_task(tool_round.persist_tool_observations(request, [record], observations))
        await asyncio.sleep(0)
        assert not saving.done()
        release_log.set()
        await saving
        await asyncio.wait_for(committed.wait(), timeout=2)
    with session_factory() as db:
        row = db.get(ToolCallLog, "log-1")
        assert row.extra_metadata["business_metadata"] == "保留"
        assert row.output_data == {"raw": "原始业务载荷"}
        detail = TrajectoryQueryService._available_tool_detail("call-1", row).detail
        assert detail.observation.status == "available"
        assert detail.observation.llm_round_id == "origin-round"
        assert "[7]" in detail.observation.text
        assert "web_search" in detail.observation.text
        assert "secret-value" not in detail.observation.text
        assert detail.observation.redacted_fields == ["observation"]
        assert detail.observation.original_chars == len(request.messages[-1]["content"])
    engine.dispose()


def test_historical_tool_feedback_is_not_reconstructed():
    row = SimpleNamespace(
        extra_metadata={},
        input_params={},
        output_data={"raw": "原始结果"},
        tool_name="weather",
        status="success",
        duration_ms=None,
        error_message=None,
    )
    detail = TrajectoryQueryService._available_tool_detail("call-old", row).detail
    assert detail.observation.status == "not_recorded"
    assert detail.observation.text is None


@pytest.mark.anyio
async def test_trim_visibility_is_actual_tool_messages_and_ledger_preserves_it():
    messages = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "old", "function": {"name": "weather"}}]},
        {"role": "tool", "tool_call_id": "old", "content": "旧资料" * 1000},
        {"role": "user", "content": "本次问题"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "visible", "function": {"name": "weather"}},
                {"id": "unreturned", "function": {"name": "weather"}},
            ],
        },
        {"role": "tool", "tool_call_id": "visible", "content": "有效资料"},
    ]
    plan = await prepare_context(
        messages=messages,
        model_id="test",
        litellm_model="test",
        call_kwargs={},
        token_estimator=lambda _, ms, __: sum(len(str(m)) for m in ms),
        window_resolver=lambda _: (1000, "test", "known"),
        run_in_thread=False,
    )
    visibility = plan.tool_visibility(messages)
    assert visibility["before_tool_call_ids"] == ["old", "visible"]
    assert visibility["visible_tool_call_ids"] == ["visible"]
    assert visibility["removed_tool_call_ids"] == ["old"]
    assert visibility["scope"] == "application_messages_after_context_management"
    event = build_trajectory_payload({"type": "llm_round_started", "context_visibility": visibility})
    assert event["context_visibility"] == visibility
    assert "旧资料" not in json.dumps(event, ensure_ascii=False)


@pytest.mark.anyio
async def test_stalled_log_does_not_block_tool_round_cleanup():
    record = _record()
    request = _request(record)
    release = asyncio.Event()

    async def stalled_log():
        await release.wait()

    record.result.trajectory_log_task = asyncio.create_task(stalled_log())
    messages = tool_round.append_tool_round_messages_with_plan(request, [record], source_plan=None)
    try:
        await asyncio.wait_for(tool_round.persist_tool_observations(request, [record], messages), timeout=0.2)
        assert request.messages[-1]["content"] == messages["call-1"]
        assert record.result.status == "failed"
    finally:
        record.result.trajectory_log_task.cancel()
        await asyncio.gather(record.result.trajectory_log_task, return_exceptions=True)


@pytest.mark.parametrize("body", ["正文" * 50000, "word " * 50000])
def test_observation_snapshot_keeps_business_text_and_bounds_redacted_content(body):
    from app.schemas.trajectory import ToolObservation
    from app.services.tool_detail_snapshot import build_tool_observation_snapshot

    original = (
        "正文前缀 https://example.com/path?api_key=hidden-key cookie: sid=hidden-cookie; session=hidden-session\n"
        + body
    )
    snapshot = build_tool_observation_snapshot(original, step_number=3)
    detail = ToolObservation.model_validate(snapshot)
    assert detail.status == "available"
    assert "正文前缀" in detail.text
    assert all(secret not in detail.text for secret in ["hidden-key", "hidden-cookie", "hidden-session"])
    assert detail.truncated_fields == ["observation"]
    assert detail.redacted_fields == ["observation"]
    assert detail.original_chars == len(original)
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) < 52 * 1024


@pytest.mark.anyio
async def test_initial_log_exception_does_not_change_tool_messages():
    record = _record()
    request = _request(record)

    async def failed_log():
        raise RuntimeError("数据库不可用")

    record.result.trajectory_log_task = asyncio.create_task(failed_log())
    observations = tool_round.append_tool_round_messages_with_plan(request, [record], source_plan=None)
    await tool_round.persist_tool_observations(request, [record], observations)
    assert request.messages[-1]["content"] == observations["call-1"]
    assert record.result.data == {"raw": "原始业务载荷"}


def test_visibility_truncation_preserves_counts_and_removal_identity():
    from app.services.chat.context_manager import ContextPlan

    before = [{"role": "tool", "tool_call_id": f"call-{i}", "content": ""} for i in range(250)]
    plan = ContextPlan(
        messages=before[-2:],
        status="trimmed",
        context_window_tokens=1000,
        context_window_source="test",
        context_window_status="known",
    )
    snapshot = plan.tool_visibility(before)
    assert snapshot["before_count"] == 250
    assert snapshot["removed_count"] == 248
    assert len(snapshot["before_tool_call_ids"]) == 200
    assert snapshot["visible_tool_call_ids"] == ["call-248", "call-249"]
    assert snapshot["truncated"] is True
    assert (
        build_trajectory_payload({"type": "llm_round_started", "context_visibility": snapshot})["context_visibility"]
        == snapshot
    )


def test_broken_available_observation_is_marked_failed_instead_of_empty_success():
    row = SimpleNamespace(
        extra_metadata={"tool_observation": {"status": "available"}},
        input_params={},
        output_data={"raw": "保留原结果"},
        tool_name="weather",
        status="success",
        duration_ms=None,
        error_message=None,
    )
    detail = TrajectoryQueryService._available_tool_detail("call-invalid", row).detail
    assert detail.observation.status == "capture_failed"


def test_maximum_context_visibility_survives_ledger_without_secondary_trimming():
    from app.services.chat.context_manager import ContextPlan

    before = [{"role": "tool", "tool_call_id": f"{i:04d}" + "x" * 124, "content": ""} for i in range(400)]
    plan = ContextPlan(
        messages=before[200:],
        status="trimmed",
        context_window_tokens=1000,
        context_window_source="test",
        context_window_status="known",
    )
    snapshot = plan.tool_visibility(before)
    event = build_trajectory_payload({"type": "llm_round_started", "context_visibility": snapshot})
    assert event["context_visibility"] == snapshot
    assert len(json.dumps(event).encode()) < 80 * 1024
    assert snapshot["truncated"] is True
    assert all(len(snapshot[f"{name}_tool_call_ids"]) == 200 for name in ["before", "visible", "removed"])


def test_credential_crossing_text_capture_boundary_is_redacted_before_truncation():
    from app.services.tool_detail_snapshot import build_tool_observation_snapshot

    for key in ["api_key", "cookie"]:
        snapshot = build_tool_observation_snapshot(f'业务正文 {key}="' + "privatevalue" * 4000 + '"', step_number=1)
        assert "privatevalue" not in snapshot["text"]
        assert snapshot["redacted_fields"] == ["observation"]
        assert snapshot["truncated_fields"] == ["observation"]


@pytest.mark.anyio
async def test_blocked_observation_database_isolated_from_default_executor_and_keeps_capacity():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.services.agent.trajectory_recorder import TRAJECTORY_MAX_WORKERS

    loop = asyncio.get_running_loop()
    unrelated_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="business-test")
    loop.set_default_executor(unrelated_workers)
    release = threading.Event()
    all_writes_finished = asyncio.Event()
    started = []
    finished = []

    def blocked_database_write(**kwargs):
        started.append(kwargs["tool_call_id"])
        release.wait(10)
        finished.append(kwargs["tool_call_id"])
        if len(finished) == TRAJECTORY_MAX_WORKERS:
            loop.call_soon_threadsafe(all_writes_finished.set)

    async def completed_initial_log():
        return None

    async def send_batch(prefix):
        records = []
        observations = {}
        for index in range(TRAJECTORY_MAX_WORKERS):
            record = _record()
            record.tool_call["id"] = f"{prefix}-{index}"
            record.result.trajectory_log_task = asyncio.create_task(completed_initial_log())
            records.append(record)
            observations[record.tool_call["id"]] = "普通业务反馈"
        await tool_round.persist_tool_observations(_request(records[0]), records, observations)

    try:
        with patch.object(tool_round, "attach_tool_observation", blocked_database_write):
            await send_batch("first")
            value = await asyncio.wait_for(asyncio.to_thread(lambda: "必要业务仍可执行"), timeout=0.3)
            assert value == "必要业务仍可执行"
            await asyncio.wait_for(asyncio.gather(*list(tool_round._PENDING_OBSERVATION_WRITES)), timeout=1)
            assert not release.is_set()
            await send_batch("second")
            await asyncio.wait_for(asyncio.gather(*list(tool_round._PENDING_OBSERVATION_WRITES)), timeout=1)
            assert len(started) == TRAJECTORY_MAX_WORKERS
            assert finished == []
            release.set()
            await asyncio.wait_for(all_writes_finished.wait(), timeout=2)
    finally:
        release.set()
        await asyncio.gather(*list(tool_round._PENDING_OBSERVATION_WRITES), return_exceptions=True)
        unrelated_workers.shutdown(wait=True)
