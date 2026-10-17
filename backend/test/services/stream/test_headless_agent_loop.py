"""脱离聊天运行 agent loop 的契约：不碰对话实时流、不落聊天消息，也能跑完多轮工具调用。

子代理、后台任务等非聊天调用方依赖这条契约；loop 核心若重新直连 Redis 或聊天持久化，这里会失败。
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent.emitter import AgentEventEmitter
from app.services.stream.agent_loop_driver import run_agent_loop
from app.services.stream.agent_loop_outcome import AgentLoopExit
from app.services.stream.agent_loop_policy import AgentLoopLimits
from app.services.stream.agent_loop_runtime import AgentLoopRuntime
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import run_agent_round
from app.services.stream.limit_summary import run_limit_summary_step
from app.services.stream.llm_stream import stream_round
from app.services.stream.step_lifecycle import complete_agent_step, start_agent_step
from app.services.stream.tool_executor import execute_tools_parallel
from app.services.stream.tool_round import handle_tool_calls_round
from app.services.tool_handlers.base import ToolResult
from test.fake_tool_handler import FakeToolHandler


class CollectingOutput:
    """把模型正文收集在内存里，代替对话实时流。"""

    def __init__(self):
        self.chunks: list[tuple[str, str]] = []

    async def append(self, chunk_type, content, block_id, **extras):
        self.chunks.append((chunk_type, content))
        return None

    async def owns_stream(self):
        return True

    def text(self, chunk_type: str) -> str:
        return "".join(content for kind, content in self.chunks if kind == chunk_type)


class CollectingEventWriter:
    """代替 AgentEventRedisWriter 收集 agent 事件。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def append_chunk(self, conversation_id, task_id, chunk_type, payload):
        self.events.append((chunk_type, payload))

    def event_types(self) -> list[str]:
        return [payload.get("type") for _, payload in self.events]


class LookupHandler(FakeToolHandler):
    tool_name = "lookup"

    def __init__(self):
        self.calls = []

    async def execute(self, args, context=None):
        self.calls.append(args)
        return ToolResult(status="success", data={"answer": "42"})

    async def log(self, **_kwargs):
        return None

    def format_llm_context(self, result, *, citation_numbers=None):
        return f"lookup 结果：{result.data['answer']}"


def _chunk(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=None)


def _tool_call_delta(tool_call_id: str, name: str, arguments: dict):
    return [
        SimpleNamespace(
            index=0,
            id=tool_call_id,
            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
        )
    ]


async def _stream(chunks):
    for chunk in chunks:
        yield chunk


class ScriptedModel:
    """第一轮调用 lookup，第二轮根据工具结果作答。"""

    def __init__(self):
        self.requests: list[list[dict]] = []

    async def __call__(self, litellm_model, litellm_kwargs, messages, **call_kwargs):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return _stream(
                [
                    _chunk(tool_calls=_tool_call_delta("call-1", "lookup", {"q": "答案"})),
                    _chunk(finish_reason="tool_calls"),
                ]
            )
        return _stream([_chunk(content="答案是 42。"), _chunk(finish_reason="stop")])


def _tool_definition(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
    }


class HeadlessAgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_tool_round_and_answer_without_chat_stream_or_persistence(self):
        output = CollectingOutput()
        writer = CollectingEventWriter()
        handler = LookupHandler()
        model = ScriptedModel()
        persisted = []
        emitter = AgentEventEmitter(
            run_id="run-headless",
            trace_id="trace-headless",
            conversation_id="conv-headless",
            task_id="task-headless",
            redis_writer=writer,
        )
        # 聊天入口由 lifecycle 发 run_started；脱离聊天的调用方自己开 run。
        await emitter.run_started(message_id="msg-headless", model="model-headless", tools=["lookup"], config={})
        runtime = AgentLoopRuntime(
            conversation_id="conv-headless",
            task_id="task-headless",
            run_id="run-headless",
            user_id="user-headless",
            model_id="model-headless",
            provider="openai",
            litellm_model="openai/model-headless",
            litellm_kwargs={},
            should_use_reasoning=False,
            call_kwargs={"tools": [_tool_definition("lookup")]},
            assistant_message_id="msg-headless",
            run_start=0.0,
            limits=AgentLoopLimits(max_steps=4, max_tool_calls=4, total_timeout_s=300),
            emitter=emitter,
            output=output,
            session_cache=AsyncMock(),
            network_budget=None,
            start_step_fn=start_agent_step,
            complete_step_fn=complete_agent_step,
            run_round_fn=run_agent_round,
            handle_tool_calls_round_fn=handle_tool_calls_round,
            run_limit_summary_step_fn=run_limit_summary_step,
            llm_call_fn=model,
            stream_round_fn=stream_round,
            execute_tools_fn=execute_tools_parallel,
            persist_message_fn=lambda *args, **kwargs: persisted.append((args, kwargs)),
            log_round_summary_fn=lambda **_kwargs: None,
            warning_fn=lambda _message: None,
            clock=lambda: 1.0,
            dynamic_tool_handlers={"lookup": handler},
        )

        state = AgentLoopState()
        chat_stream_write = AsyncMock(side_effect=AssertionError("loop 不应直写对话实时流"))
        with (
            patch("app.services.stream_state_service.append_chunk", chat_stream_write),
            patch("app.services.stream_state_service.check_lock_owner", chat_stream_write),
        ):
            outcome = await run_agent_loop(
                db=None,
                messages=[{"role": "user", "content": "答案是多少？"}],
                state=state,
                runtime=runtime,
            )

        self.assertEqual(outcome.exit, AgentLoopExit.COMPLETED)
        self.assertEqual(handler.calls, [{"q": "答案"}])
        self.assertEqual(len(model.requests), 2)
        self.assertIn("lookup 结果：42", json.dumps(model.requests[1], ensure_ascii=False))
        self.assertEqual(output.text("answering"), "答案是 42。")
        self.assertIn("tool_call_started", writer.event_types())
        self.assertIn("tool_call_completed", writer.event_types())
        chat_stream_write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
