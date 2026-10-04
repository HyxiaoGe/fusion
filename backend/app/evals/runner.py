"""在 fusion-api 进程内驱动真实对话并判分。

走 ChatService.process_message 的完整链路（分类、Agent loop、工具、落库），只绕过 HTTP 鉴权；
读完整个 SSE 流等价于一个前端客户端。评测会话归专用评测用户，与真实用户、探针账号互不混淆。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentSession, User
from app.evals.cases import EvalCase, JudgeCheck
from app.evals.checks import CheckOutcome, Judge, evaluate, overall_status
from app.evals.trajectory import JUDGE_TOOL_RESULT_CHAR_LIMIT, load_snapshot

EVAL_USERNAME = "fusion-eval-suite"
CASE_TIMEOUT_SECONDS = 900
TERMINAL_WAIT_SECONDS = 60
_TERMINAL_POLL_SECONDS = 1.0


@dataclass
class CaseOutcome:
    case_id: str
    model_id: str
    status: str
    checks: list[CheckOutcome] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    error: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    duration_ms: int = 0
    attempt: int = 1


@dataclass
class _TurnResult:
    run_id: str | None = None
    conversation_id: str | None = None
    errors: list[str] = field(default_factory=list)


def ensure_eval_user(db: Session) -> str:
    user = db.execute(select(User).where(User.username == EVAL_USERNAME)).scalar_one_or_none()
    if user is None:
        user = User(username=EVAL_USERNAME, nickname="回放评测")
        db.add(user)
        db.commit()
    return user.id


def parse_sse_frame(frame: str, turn: _TurnResult) -> None:
    """只认 run_started 的关联 id 与 error 帧，其余内容以落库的轨迹为准。"""
    for line in frame.splitlines():
        if not line.startswith("data: "):
            continue
        body = line[len("data: ") :]
        if body == "[DONE]":
            continue
        try:
            envelope = json.loads(body)
        except json.JSONDecodeError:
            continue
        data = envelope.get("data") or {}
        if envelope.get("chunk_type") == "error":
            turn.errors.append(str(data.get("message") or data.get("code") or data)[:300])
        elif envelope.get("chunk_type") == "agent_event" and data.get("type") == "run_started":
            turn.run_id = turn.run_id or data.get("run_id")
            turn.conversation_id = turn.conversation_id or data.get("conversation_id")


async def _send_turn(
    db: Session, turn: _TurnResult, *, model_id: str, message: str, user_id: str, options: dict
) -> None:
    """边读流边填 turn，超时被取消时已拿到的 run_id 不会丢。"""
    from app.services.chat_service import ChatService

    response = await ChatService(db).process_message(
        model_id=model_id,
        message=message,
        user_id=user_id,
        conversation_id=turn.conversation_id,
        stream=True,
        options=dict(options),
    )
    buffer = ""
    async for chunk in response.body_iterator:
        buffer += chunk.decode() if isinstance(chunk, bytes) else chunk
        *frames, buffer = buffer.split("\n\n")
        for frame in frames:
            parse_sse_frame(frame, turn)
    if buffer:
        parse_sse_frame(buffer, turn)


async def _wait_terminal(session_factory: Callable[[], Session], run_id: str) -> str | None:
    deadline = time.monotonic() + TERMINAL_WAIT_SECONDS
    while True:
        with session_factory() as db:
            status = db.execute(select(AgentSession.status).where(AgentSession.id == run_id)).scalar_one_or_none()
        if status not in (None, "running") or time.monotonic() >= deadline:
            return status
        await asyncio.sleep(_TERMINAL_POLL_SECONDS)


async def _wait_generation(conversation_id: str | None) -> None:
    """会话状态先于轨迹屏障（seal + finalize）落库：还要等生成任务本身结束，
    否则最后一条用例刚判完就关闭运行时，会取消仍在收尾的屏障。"""
    from app.services.task_manager import get_task

    task = get_task(conversation_id) if conversation_id else None
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=TERMINAL_WAIT_SECONDS)


async def _drive(
    case: EvalCase,
    model_id: str,
    turns: list[_TurnResult],
    *,
    user_id: str,
    session_factory: Callable[[], Session],
) -> None:
    conversation_id = None
    with session_factory() as db:
        for message in [*case.setup_turns, case.message]:
            turn = _TurnResult(conversation_id=conversation_id)
            turns.append(turn)
            await _send_turn(db, turn, model_id=model_id, message=message, user_id=user_id, options=case.options)
            conversation_id = turn.conversation_id or conversation_id
            if turn.run_id:
                await _wait_terminal(session_factory, turn.run_id)
            await _wait_generation(conversation_id)


async def _stop_generation(conversation_id: str) -> None:
    """与前端"停止生成"相同的取消路径，run 以 interrupted 正常收尾，而不是被关闭运行时打断。"""
    from app.services.stream_state_service import cancel_stream
    from app.services.task_manager import cancel_task

    cancel_task(conversation_id)
    await cancel_stream(conversation_id)


async def run_case(
    case: EvalCase,
    model_id: str,
    *,
    user_id: str,
    session_factory: Callable[[], Session],
    judge: Judge | None,
    attempt: int = 1,
) -> CaseOutcome:
    started = time.monotonic()
    turns: list[_TurnResult] = []
    outcome = CaseOutcome(case.id, model_id, "error", attempt=attempt)
    timed_out = False
    try:
        await asyncio.wait_for(
            _drive(case, model_id, turns, user_id=user_id, session_factory=session_factory),
            timeout=CASE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as exc:  # 单条用例的基础设施故障不应中断整轮评测
        outcome.error = f"{type(exc).__name__}: {exc}"[:500]

    turn = turns[-1] if turns else _TurnResult()
    outcome.run_id, outcome.conversation_id = turn.run_id, turn.conversation_id
    if timed_out and turn.conversation_id:
        await _stop_generation(turn.conversation_id)
        if turn.run_id:
            await _wait_terminal(session_factory, turn.run_id)
        await _wait_generation(turn.conversation_id)

    try:
        if outcome.error is None:
            await _judge_turn(
                outcome,
                case,
                turn,
                final=len(turns) == len(case.setup_turns) + 1,
                timed_out=timed_out,
                session_factory=session_factory,
                judge=judge,
            )
    except Exception as exc:
        outcome.status, outcome.error = "error", f"{type(exc).__name__}: {exc}"[:500]
    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    return outcome


async def _judge_turn(
    outcome: CaseOutcome,
    case: EvalCase,
    turn: _TurnResult,
    *,
    final: bool,
    timed_out: bool,
    session_factory: Callable[[], Session],
    judge: Judge | None,
) -> None:
    snapshot = None
    if turn.run_id:
        with session_factory() as db:
            snapshot = load_snapshot(db, turn.run_id)
        if snapshot is not None and turn.errors:
            snapshot["stream_errors"] = turn.errors
    outcome.snapshot = snapshot
    if timed_out:
        # 产品自身上限远大于此；一轮对话这么久还没结束，用户早已离开，算被测链路失败。
        stage = "本轮" if final else "前置对话"
        outcome.status = "failed"
        outcome.checks = [
            CheckOutcome("run_status", "failed", f"{stage}超过 {CASE_TIMEOUT_SECONDS}s 未完成，已停止生成")
        ]
        return
    if not turn.run_id:
        outcome.error = "未拿到 run_id：" + ("; ".join(turn.errors) or "流里没有 run_started")
        return
    if snapshot is None:
        outcome.error = f"轨迹账本里找不到 run {turn.run_id}"
        return
    judge_snapshot = None
    if judge is not None and any(isinstance(check, JudgeCheck) for check in case.checks):
        with session_factory() as db:
            judge_snapshot = load_snapshot(db, turn.run_id, result_limit=JUDGE_TOOL_RESULT_CHAR_LIMIT)
    outcome.checks = await evaluate(case, snapshot, judge, judge_snapshot=judge_snapshot)
    outcome.status = overall_status(outcome.checks)


@asynccontextmanager
async def eval_runtime():
    """与 API 进程 lifespan 相同的初始化与收尾，但不启动定时任务、不做健康探测。"""
    from app.ai import litellm_cleanup
    from app.core.redis import close_redis, init_redis
    from app.services.agent.llm_round_detail_recorder import stop_llm_round_detail_workers
    from app.services.knowledge.storage_upload_guard import shutdown_storage_upload_lifecycles
    from app.services.mcp.runtime import get_mcp_client_manager
    from app.services.prompt_catalog_integrity import verify_prompt_catalog_consumers
    from app.services.storage import init_storage
    from app.services.suggested_question_worker import stop_suggested_question_workers

    verify_prompt_catalog_consumers()
    await init_redis()
    await init_storage()
    try:
        yield
    finally:
        await shutdown_storage_upload_lifecycles()
        await stop_llm_round_detail_workers()
        await stop_suggested_question_workers()
        await litellm_cleanup.close_async_clients()
        await get_mcp_client_manager().close()
        await close_redis()
