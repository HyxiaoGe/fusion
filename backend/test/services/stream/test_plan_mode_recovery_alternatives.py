"""计划模式裁剪本轮目录后，恢复提示仍必须看得见 run 级替代工具。

真实验收（Run 77fa7280…）：高德失败后模型确实拿回了 update_plan，却没有新增
联网恢复步骤。原因是 _recovery_alternatives 以「本轮公告工具」为依据，而计划
门禁已经把 web_search/url_read 从本轮目录里摘掉，于是判定「无替代方案」，
tool_failure_recovery 提示从未注入——模型拿到了权限，却没拿到该怎么用的指引。
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.chat import Usage
from app.services.stream.agent_loop_round_outcome import (
    AgentRoundOutcomeRequest,
    _recovery_alternatives,
    handle_agent_round_outcome,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
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


if __name__ == "__main__":
    unittest.main()
