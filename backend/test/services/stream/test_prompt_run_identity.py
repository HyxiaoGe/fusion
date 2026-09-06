"""真实事务确保分类前有身份，分类完成只能补配置，不能重置运行。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import AgentSession, Conversation, User
from app.services.agent import session_cache
from app.services.stream import runner
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
from app.services.stream.run_capability_router import _CandidateRoute


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id="u1", username="owner", email="owner@example.com"))
        db.commit()
        db.add(Conversation(id="c1", user_id="u1", title="冻结验证", model_id="m1"))
        db.commit()
    with patch.object(session_cache, "SessionLocal", factory):
        yield factory
    engine.dispose()


def run_kwargs():
    return dict(
        conversation_id="c1",
        user_id="u1",
        model_id="m1",
        litellm_model="m1",
        litellm_kwargs={},
        provider="openai",
        raw_messages=[],
        has_vision=False,
        file_ids=[],
        original_message="解释递归",
        assistant_message_id="a1",
        task_id="t1",
        trace_id="r1",
        turn_message_id="turn1",
        capabilities={"functionCalling": True, "searchCapable": True},
    )


@pytest.mark.anyio
async def test_identity_commit_failure_prevents_classifier_and_business_model():
    entered = []

    def classify(**kwargs):
        entered.append("classifier")
        raise AssertionError("不应进入分类")

    with (
        patch.object(runner, "SessionLocal", return_value=MagicMock()),
        patch.object(runner, "prepare_agent_loop_call_config_inputs", return_value=SimpleNamespace()),
        patch.object(runner, "build_agent_loop_call_config_from_inputs", side_effect=classify),
        patch.object(session_cache, "write_session_started", AsyncMock(side_effect=RuntimeError("身份事务失败"))),
        patch.object(runner, "_run_agent_loop_lifecycle_call", AsyncMock(side_effect=AssertionError("不应进入主模型"))),
        patch.object(runner, "finalize_stream", AsyncMock()) as finalize,
        pytest.raises(RuntimeError, match="身份事务失败"),
    ):
        await runner.StreamHandler().generate_to_redis(**run_kwargs())
    assert entered == []
    assert finalize.await_args.kwargs["success"] is False
    assert "身份事务失败" not in finalize.await_args.kwargs["error_msg"]


@pytest.mark.anyio
async def test_classifier_observes_committed_identity_and_pre_lifecycle_error_terminates_run(session_factory):
    seen = []

    def classify(**kwargs):
        with session_factory() as db:
            row = db.get(AgentSession, "r1")
            assert row is not None, "分类前必须已经原子创建 Run"
            seen.append(row.run_config["prompt_bundle"])
        return _CandidateRoute("direct", "high", ("stable_knowledge_question",), False)

    dependencies = replace(
        runner._agent_loop_wiring_dependencies(),
        build_call_config_fn=lambda **kwargs: build_agent_loop_call_config(**kwargs, classify_fn=classify),
        load_dynamic_tools_fn=None,
        load_authorized_tool_names_fn=None,
    )
    with (
        patch.object(runner, "SessionLocal", session_factory),
        patch.object(runner, "_agent_loop_wiring_dependencies", return_value=dependencies),
        patch.object(runner, "assemble_agent_loop_lifecycle_call", side_effect=RuntimeError("组装失败")),
        pytest.raises(RuntimeError, match="组装失败"),
    ):
        await runner.StreamHandler().generate_to_redis(**run_kwargs())
    assert len(seen) == 1
    assert seen[0]["source_kind"] == "code_default"
    assert len(seen[0]["effective_revision"]) == 64
    with session_factory() as db:
        row = db.get(AgentSession, "r1")
        assert row.status == "error"
        assert row.run_config["prompt_bundle"] == seen[0]


@pytest.mark.anyio
async def test_completing_configuration_preserves_identity_and_cannot_reset_terminal_run(session_factory):
    identity = {"source_kind": "code_default", "source_revision": None, "effective_revision": "a" * 64}
    await session_cache.write_session_started(
        run_id="r1",
        conversation_id="c1",
        user_id="u1",
        model_id="m1",
        provider="openai",
        message_id="a1",
        turn_message_id="turn1",
        run_config={"prompt_bundle": identity},
    )
    await session_cache.complete_session_configuration(
        run_id="r1",
        conversation_id="c1",
        user_id="u1",
        run_config={"prompt_bundle": identity, "max_steps": 8},
    )
    with session_factory() as db:
        row = db.get(AgentSession, "r1")
        assert row.run_config == {"prompt_bundle": identity, "max_steps": 8}
        assert row.attempt_index == 1
    with pytest.raises(ValueError, match="身份"):
        await session_cache.complete_session_configuration(
            run_id="r1",
            conversation_id="c1",
            user_id="u1",
            run_config={"prompt_bundle": {**identity, "effective_revision": "b" * 64}},
        )
    await session_cache.write_session_status(run_id="r1", status="interrupted", total_steps=0, total_tool_calls=0)
    with pytest.raises(ValueError, match="终态"):
        await session_cache.complete_session_configuration(
            run_id="r1",
            conversation_id="c1",
            user_id="u1",
            run_config={"prompt_bundle": identity},
        )
    with pytest.raises(ValueError, match="不得重入"):
        await session_cache.write_session_started(
            run_id="r1",
            conversation_id="c1",
            user_id="u1",
            model_id="m1",
            provider="openai",
            message_id="a1",
            turn_message_id="turn1",
            run_config={"prompt_bundle": identity},
        )
    with session_factory() as db:
        assert db.get(AgentSession, "r1").status == "interrupted"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_classifier_cancellation_expires_gate_and_terminates_identity_run(session_factory):
    with (
        patch.object(runner, "SessionLocal", session_factory),
        patch.object(runner, "prepare_agent_loop_call_config_inputs", return_value=SimpleNamespace()),
        patch.object(runner, "build_agent_loop_call_config_from_inputs", side_effect=asyncio.CancelledError()),
        patch.object(runner, "ClassifierDeadlineGate") as gate,
        patch.object(runner, "finalize_stream", AsyncMock()) as finalize,
        patch.object(runner, "_run_agent_loop_lifecycle_call", AsyncMock()) as lifecycle,
        pytest.raises(asyncio.CancelledError),
    ):
        await runner.StreamHandler().generate_to_redis(**run_kwargs())
    gate.return_value.expire.assert_called_once()
    lifecycle.assert_not_awaited()
    assert finalize.await_args.kwargs["error_code"] == "stream_interrupted"
    with session_factory() as db:
        assert db.get(AgentSession, "r1").status == "interrupted"


@pytest.mark.anyio
@pytest.mark.parametrize("attempt_kind", ["retry", "regenerate", "continue"])
async def test_new_attempt_refreezes_source_and_preserves_lineage(session_factory, attempt_kind):
    snapshot_a = runner.freeze_runtime_prompt_bundle()
    snapshot_b = replace(snapshot_a, effective_revision="b" * 64)
    with (
        patch.object(runner, "SessionLocal", session_factory),
        patch.object(runner, "freeze_runtime_prompt_bundle", side_effect=[snapshot_a, snapshot_b]),
        patch.object(runner, "prepare_agent_loop_call_config_inputs", return_value=SimpleNamespace()),
        patch.object(runner, "build_agent_loop_call_config_from_inputs", return_value=SimpleNamespace()),
        patch.object(runner, "assemble_agent_loop_lifecycle_call", side_effect=RuntimeError("组装失败")),
        patch.object(runner, "finalize_stream", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="组装失败"):
            await runner.StreamHandler().generate_to_redis(**run_kwargs())
        await session_cache.write_session_status(
            run_id="r1",
            status="limit_reached" if attempt_kind == "continue" else "interrupted",
            total_steps=0,
            total_tool_calls=0,
        )
        with pytest.raises(RuntimeError, match="组装失败"):
            await runner.StreamHandler().generate_to_redis(
                **{**run_kwargs(), "trace_id": "r2", "previous_run_id": "r1", "run_attempt_kind": attempt_kind}
            )
    with session_factory() as db:
        first, second = db.get(AgentSession, "r1"), db.get(AgentSession, "r2")
        assert first.run_config["prompt_bundle"] == snapshot_a.identity()
        assert second.run_config["prompt_bundle"] == snapshot_b.identity()
        assert second.previous_run_id == "r1"
        assert second.attempt_index == 2
