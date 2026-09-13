"""计划模式裁剪本轮目录后，恢复提示仍必须看得见 run 级替代工具。

真实验收（Run 77fa7280…）：高德失败后模型确实拿回了 update_plan，却没有新增
联网恢复步骤。原因是 _recovery_alternatives 以「本轮公告工具」为依据，而计划
门禁已经把 web_search/url_read 从本轮目录里摘掉，于是判定「无替代方案」，
tool_failure_recovery 提示从未注入——模型拿到了权限，却没拿到该怎么用的指引。

但 _recovery_alternatives 只在 finish_reason == "stop" 分支执行，而工具失败那一轮
返回的是 tool_calls，所以修正集合来源并不能让恢复规划轮提前看到提示。真正能在
恢复规划轮之前给出指引的位置是失败 Observation（tool_round 拼接 tool 消息时），
它同样只看本轮目录。下面第二组用例覆盖这条时序。
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import Usage
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import (
    _filter_tools_for_research_stage,
    resolve_plan_mode_allowed_tools,
)
from app.services.stream.agent_loop_round_outcome import (
    AgentRoundOutcomeRequest,
    _recovery_alternatives,
    handle_agent_round_outcome,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import (
    ToolRoundRequest,
    _partition_tool_calls_by_announcement,
    append_tool_round_messages,
)
from app.services.tool_handlers.base import ToolResult
from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context

_RUN_TOOLS = ("weather_forecast", "web_search", "url_read")


def _tools_payload(names):
    return [{"type": "function", "function": {"name": name, "parameters": {}}} for name in names]


def _request(state, *, round_tools, run_tools=_RUN_TOOLS):
    return AgentRoundOutcomeRequest(
        db=None,
        messages=[{"role": "user", "content": "香港未来三天会下雨吗"}],
        state=state,
        runtime=_runtime(
            emitter=AsyncMock(),
            complete_step_fn=AsyncMock(),
            call_kwargs={"tools": _tools_payload(run_tools)},
            plan_mode="on",
        ),
        step_number=2,
        step_context=_step_context(),
        round_result=AgentRoundResult(
            reasoning_buf="",
            content_buf="天气查询失败，请稍后再试。",
            tool_calls=[],
            finish_reason="stop",
            accumulated_usage=Usage(input_tokens=0, output_tokens=0),
            context=None,
            output_deferred=True,
            announced_tool_names=frozenset(round_tools),
        ),
    )


class PlanModeRecoveryAlternativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_本轮目录被裁剪时仍注入恢复提示(self):
        """复现验收场景：本轮只公告了已失败的产品工具。"""
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        request = _request(state, round_tools=("weather_forecast",))

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            self.assertIsNone(await handle_agent_round_outcome(request=request))

        self.assertTrue(state.tool_recovery_prompted)
        self.assertEqual(request.messages[-1].section_id, "tool_failure_recovery")
        self.assertIn("web_search", request.messages[-1]["content"])
        self.assertIn("url_read", request.messages[-1]["content"])

    async def test_已尝试过的工具不算替代方案(self):
        """直接断言替代集合：提示正文里 web_search 会作为失败工具出现，
        不能用正文包含与否来判断它是否被当成替代方案。"""
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        state.record_tool_outcome("web_search", "failed")
        request = _request(state, round_tools=("weather_forecast",))

        self.assertEqual(_recovery_alternatives(request), {"url_read"})

    async def test_run_级也无替代工具时不注入(self):
        """产品工具是本次运行唯一外部工具时，没有可推荐的替代路径。"""
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        request = _request(
            state,
            round_tools=("weather_forecast",),
            run_tools=("weather_forecast",),
        )

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)

        self.assertFalse(state.tool_recovery_prompted)

    async def test_非计划模式行为不变(self):
        """本轮目录未被裁剪时，结论与此前一致。"""
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        request = _request(state, round_tools=_RUN_TOOLS)

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock()):
            await handle_agent_round_outcome(request=request)

        self.assertTrue(state.tool_recovery_prompted)
        self.assertIn("web_search", request.messages[-1]["content"])


_WEATHER_CALL = {
    "id": "tc-weather",
    "name": "weather_forecast",
    "arguments": {"city": "香港", "days": 3},
    "plan_item_id": "w",
}


def _initial_plan():
    return {
        "reason": "先查天气再回答",
        "items": [
            {
                "id": "w",
                "title": "查天气",
                "status": "pending",
                "kind": "search",
                "depends_on": [],
                "planned_tools": ["weather_forecast"],
            },
            {
                "id": "a",
                "title": "回答",
                "status": "pending",
                "kind": "answer",
                "depends_on": ["w"],
                "planned_tools": [],
            },
        ],
    }


def _failed_weather_record():
    return ToolExecutionRecord(
        tool_call=dict(_WEATHER_CALL),
        result=ToolResult(status="failed", error_message="高德接口失败"),
        handler=None,
        block_id="blk-weather",
        log_id="log-weather",
    )


class FailureObservationRecoveryGuidanceTests(unittest.TestCase):
    """工具失败 → 指引进入消息 → 下一轮同时拿到指引与 update_plan。

    这是真实验收缺的那一环：第 2 轮返回 tool_calls 而非 stop，恢复规划轮（第 3 轮）
    之前唯一能塞进指引的位置就是失败 Observation。计划走真实 coordinator 链路，
    工具目录用驱动循环自己的门禁函数推导，不复制判断逻辑。
    """

    def setUp(self):
        self.coordinator = PlanCoordinator(run_id="run-1", mode="on", allowed_tool_names=frozenset(_RUN_TOOLS))
        self.assertTrue(self.coordinator.apply_model_update(_initial_plan()).accepted)
        self.state = AgentLoopState()
        self.state.plan_coordinator = self.coordinator

    def _request(self, *, tool_calls=None, run_tools=_RUN_TOOLS, agent_state=...):
        return ToolRoundRequest(
            db=None,
            assistant_message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            model_id="deepseek-chat",
            provider="deepseek",
            content_blocks=[],
            messages=[{"role": "user", "content": "香港未来三天会下雨吗"}],
            tool_calls=[dict(_WEATHER_CALL)] if tool_calls is None else tool_calls,
            reasoning_buf="",
            should_use_reasoning=False,
            step_context=_step_context(),
            step_number=2,
            run_id="run-1",
            emitter=AsyncMock(),
            session_cache=object(),
            network_budget=object(),
            call_kwargs={"tools": _tools_payload((*run_tools, "update_plan"))},
            persist_message_fn=Mock(),
            execute_tools_fn=AsyncMock(),
            complete_step_fn=AsyncMock(),
            # 计划门禁裁剪后的本轮目录：模型第 2 轮只看得见计划内的产品工具。
            announced_tool_names=frozenset({"weather_forecast"}),
            agent_state=self.state if agent_state is ... else agent_state,
        )

    def _observation(self, request):
        return next(message for message in request.messages if message.get("role") == "tool")

    def _offered_next_round(self):
        allowed = resolve_plan_mode_allowed_tools(self.coordinator)
        filtered = _filter_tools_for_research_stage(
            {"tools": _tools_payload((*_RUN_TOOLS, "update_plan"))},
            allowed_tool_names=allowed,
        )
        return [tool["function"]["name"] for tool in filtered.get("tools") or []]

    def test_失败观察同时给出run级替代工具与改计划指引(self):
        """复现验收时序：第 2 轮 tool_calls 失败，第 3 轮拿到 update_plan 与指引。"""
        request = self._request()
        self.coordinator.mark_tools_started(["w"])
        append_tool_round_messages(request, [_failed_weather_record()])
        self.coordinator.mark_tool_results({"w": "failed"})

        observation = self._observation(request)
        self.assertIn("web_search", observation["content"])
        self.assertIn("url_read", observation["content"])
        self.assertIn(render_runtime_prompt("stream.plan_hint_recovery"), observation["content"])

        # 下一轮：门禁交还 update_plan，且上一轮的指引仍留在消息里。
        self.assertEqual(self._offered_next_round(), ["update_plan"])
        self.assertIn(observation, request.messages)

    def test_指引不放行本轮未授权的工具调用(self):
        """放宽只作用于指引文本；合法性仍按本轮公告判定。"""
        request = self._request(tool_calls=[{"id": "tc-search", "name": "web_search", "arguments": {}}])

        announced, unavailable = _partition_tool_calls_by_announcement(request)

        self.assertEqual(announced, [])
        self.assertEqual([call["name"] for call in unavailable], ["web_search"])

    def test_非计划模式不追加改计划指引(self):
        """没有激活计划时，模型可以直接改调替代工具，不需要先改计划。"""
        request = self._request(agent_state=None)

        append_tool_round_messages(request, [_failed_weather_record()])

        observation = self._observation(request)
        self.assertIn("web_search", observation["content"])
        self.assertNotIn(render_runtime_prompt("stream.plan_hint_recovery"), observation["content"])

    def test_run级也无替代工具时不追加任何指引(self):
        request = self._request(run_tools=("weather_forecast",))

        append_tool_round_messages(request, [_failed_weather_record()])

        observation = self._observation(request)
        self.assertNotIn("url_read", observation["content"])
        self.assertNotIn(render_runtime_prompt("stream.plan_hint_recovery"), observation["content"])


if __name__ == "__main__":
    unittest.main()
