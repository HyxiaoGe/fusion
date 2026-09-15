"""未执行的工具协议不能作为有效答案被标成功。

真实样本 Run e96ab8c9（线上 5cdc645）：高德失败 → 恢复计划被接受 → web_search 执行成功，
第五轮工具目录为空，模型吐出一段未执行的「更新计划」协议，运行却被标为 stop / 已完成。

当时的探测器只认精确字面量 <｜｜DSML｜｜tool_calls>，以及「截断在缓冲区末尾」的半截前缀。
供应商实际吐出的是 <｜｜DSML｜｜ calls> —— 完整但变体，出现在正文中间，
正好从两条臂之间漏过去，于是同时绕过探测、解析与清理。

口径：识别放宽，恢复严格。绝不把中文工具名回译后执行。
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.chat import Usage
from app.services.stream.agent_loop_round_outcome import (
    PROTOCOL_RESIDUE_ANSWER_TEXT,
    AgentRoundOutcomeRequest,
    handle_agent_round_outcome,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.llm_stream import (
    contains_tool_protocol_residue,
    parse_dsml_tool_calls,
    strip_pending_dsml_tool_protocol,
)

# Run e96ab8c9 数据库最终正文的开头；完整正文 909 字符，结构相同。
REAL_VARIANT = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="更新计划">\n'
    '<｜｜DSML｜｜ parameter name="explanation" string="true">'
    "搜索到的多个预报来源数值存在出入，需要读取权威原始页面核对未来三天数据。"
    '</｜｜DSML｜｜ parameter>\n'
    '<｜｜DSML｜｜ parameter name="plan" string="false">'
    '[{"depends_on": [], "id": "forecast", "kind": "search", "预计使用的工具": ["weather_forecast"], '
    '"step": "获取香港未来三天的天气预报数据"}]'
    '</｜｜DSML｜｜ parameter>\n'
    '</｜｜DSML｜｜ invoke>\n'
    '</｜｜DSML｜｜ calls>'
)

EXACT_MARKER = '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="web_search">'


class ProtocolResidueIdentificationTests(unittest.TestCase):
    """识别放宽：不认具体标签名，只认哨兵与结构。"""

    def test_真实变体被识别为残留(self):
        self.assertTrue(contains_tool_protocol_residue(REAL_VARIANT))

    def test_精确旧标记仍被识别(self):
        self.assertTrue(contains_tool_protocol_residue(EXACT_MARKER))

    def test_清理把变体整段移除(self):
        self.assertEqual(strip_pending_dsml_tool_protocol(REAL_VARIANT, final=True), "")

    def test_正文在前协议在后时只截掉协议(self):
        text = f"香港未来三天多云。\n\n{REAL_VARIANT}"

        self.assertEqual(strip_pending_dsml_tool_protocol(text, final=True), "香港未来三天多云。\n\n")

    def test_通用协议形状仍被识别(self):
        self.assertTrue(contains_tool_protocol_residue('<tool_call>\n<function=web_search>'))


class StrictRecoveryTests(unittest.TestCase):
    """恢复严格：识别放宽不等于放宽解析，中文工具名一律不回译执行。"""

    def test_变体不被解析成可执行工具调用(self):
        self.assertEqual(parse_dsml_tool_calls(REAL_VARIANT, id_prefix="run"), [])

    def test_中文工具名不被映射回update_plan(self):
        calls = parse_dsml_tool_calls(REAL_VARIANT, id_prefix="run")

        self.assertNotIn("update_plan", [call.get("name") for call in calls])


class ProtocolResidueFalsePositiveTests(unittest.TestCase):
    """判正会把整次运行变成非成功终态，正常答复不能被误伤。"""

    def test_正常天气答复(self):
        answer = "香港未来三天多云，气温 26–30 度，湿度偏高。晚间适合散步，建议带伞。来源 [1][2]。"

        self.assertFalse(contains_tool_protocol_residue(answer))

    def test_普通技术引用(self):
        answer = "可以看 XML 的 <parameter> 元素，或者把 <tool> 当作比喻来理解。"

        self.assertFalse(contains_tool_protocol_residue(answer))

    def test_代码块里讨论协议不算残留(self):
        answer = "协议格式如下：\n\n```\n<｜｜DSML｜｜tool_calls>\n```\n\n以上仅为示例。"

        self.assertFalse(contains_tool_protocol_residue(answer))

    def test_结尾半截尖括号不判正(self):
        """流式探测可以宽到这一步，提交守卫不行——代价是把正常答复变成非成功。"""
        self.assertFalse(contains_tool_protocol_residue("剩下的判断留给下一轮 <t"))

    def test_空正文不判正(self):
        self.assertFalse(contains_tool_protocol_residue(""))


def _search_result(url: str):
    return {
        "status": "success",
        "data": {
            "sources": [{"url": url, "content": "香港天文台：未来三天多云，26 至 30 度。"}],
            "context_source_count": 1,
        },
    }


def _search_block(url: str):
    return {
        "type": "search",
        "status": "success",
        "source_refs": [{"url": url, "status": "success", "title": "香港天文台", "citation_index": 1}],
    }


class RecoveryAnswerBranchTests(unittest.IsolatedAsyncioTestCase):
    """恢复答复分支不能凭「有搜索证据」放行协议文本。

    这条分支是真实样本实际走的那条：is_grounded_recovery_answer 只检查
    「答复非空」+「有成功的检索证据块」，两条都满足，于是原样提交并标成功。
    """

    def _request(self, content_buf: str) -> AgentRoundOutcomeRequest:
        from test.services.stream.test_agent_loop_round_outcome import _runtime, _step_context

        url = "https://www.hko.gov.hk/tc/wxinfo/currwx/fnd.htm"
        state = AgentLoopState(product_tool_attempted=True)
        state.record_tool_outcome("weather_forecast", "failed")
        state.recovery_evidence.record_result("web_search", _search_result(url))
        state.content_blocks.append(_search_block(url))
        return AgentRoundOutcomeRequest(
            db=None,
            messages=[{"role": "user", "content": "香港未来三天会下雨吗"}],
            state=state,
            runtime=_runtime(emitter=AsyncMock(), complete_step_fn=AsyncMock()),
            step_number=5,
            step_context=_step_context(),
            round_result=AgentRoundResult(
                reasoning_buf="",
                content_buf=content_buf,
                tool_calls=[],
                finish_reason="stop",
                accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                context=None,
                output_deferred=True,
                announced_tool_names=frozenset(),
            ),
        )

    async def _run(self, content_buf: str):
        request = self._request(content_buf)
        committed: list[str] = []

        async def _capture(_conv_id, _channel, text, *_args, **_kwargs):
            committed.append(text)

        with patch("app.services.stream.agent_loop_round_outcome.append_chunk", AsyncMock(side_effect=_capture)):
            await handle_agent_round_outcome(request=request)
        return request, committed

    async def test_协议残留不得成为有效答案且终态非成功(self):
        """复现真实样本：证据齐备，正文却是没执行的协议。"""
        request, committed = await self._run(REAL_VARIANT)

        self.assertIn(PROTOCOL_RESIDUE_ANSWER_TEXT, committed)
        self.assertNotIn(REAL_VARIANT, committed)
        self.assertTrue(request.state.unknown_terminated, "只换掉文本、仍标成功不算修好")

    async def test_纠正后的有效答案照常成功(self):
        """识别放宽不能误伤真答案：同样的证据下，正常答复必须原样通过且保持成功终态。"""
        answer = "香港未来三天多云，气温 26–30 度。晚间适合散步，湿度偏高建议带伞。来源 [1]。"

        request, committed = await self._run(answer)

        self.assertIn(answer, committed)
        self.assertFalse(request.state.unknown_terminated)


if __name__ == "__main__":
    unittest.main()
