"""恢复计划被 uncovered_execution_branch 拒绝时必须给出可操作反馈。

真实验收 Run 8139d99f：#67 的两条恢复指引已进入第二轮 Observation（注入时序
生效），模型随后三次修改计划，三次都被 uncovered_execution_branch 拒绝，没有
执行任何联网搜索，最终 incomplete。

拒绝理由本身没有 hint 分支，回执只有错误码与 canonical_plan。模型最自然的改法
——把答复步骤改为依赖新增的恢复步骤、不再依赖已失败的步骤——恰好就是被拒的那种
结构，而它无从得知该怎么改。

全程走真实控制调用链路 process_plan_control_calls，不直接断言协调器内部状态。
"""

import json
import unittest
from unittest.mock import AsyncMock

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.agent.plan_coordinator import PlanCoordinator, uncovered_execution_tool_names
from app.services.stream.plan_control import process_plan_control_calls

_ALLOWED = frozenset({"weather_forecast", "web_search", "url_read"})
_UNCOVERED_HINT = render_runtime_prompt("stream.plan_hint_uncovered_branch")


def _item(item_id, *, kind, depends_on, planned_tools, status="pending", title="步骤"):
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "kind": kind,
        "depends_on": list(depends_on),
        "planned_tools": list(planned_tools),
    }


def _initial_plan():
    return {
        "reason": "先查天气再判断是否适合散步",
        "items": [
            _item("forecast", kind="search", depends_on=[], planned_tools=["weather_forecast"], title="查天气"),
            _item("walk", kind="answer", depends_on=["forecast"], planned_tools=[], title="给出散步建议"),
        ],
    }


def _recovery_plan(*, answer_depends_on, search_depends_on=()):
    return {
        "reason": "高德天气失败，改用联网搜索",
        "items": [
            _item(
                "forecast",
                kind="search",
                depends_on=[],
                planned_tools=["weather_forecast"],
                status="failed",
                title="查天气",
            ),
            _item(
                "search",
                kind="search",
                depends_on=search_depends_on,
                planned_tools=["web_search"],
                title="搜索香港天气",
            ),
            _item("walk", kind="answer", depends_on=answer_depends_on, planned_tools=[], title="给出散步建议"),
        ],
    }


class UncoveredExecutionBranchHintTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = PlanCoordinator(run_id="run-1", mode="on", allowed_tool_names=_ALLOWED)
        self.assertTrue(self.coordinator.apply_model_update(_initial_plan()).accepted)
        self.coordinator.mark_tools_started(["forecast"])
        self.coordinator.mark_tool_results({"forecast": "failed"})

    async def _submit(self, payload):
        result = await process_plan_control_calls(
            tool_calls=[{"id": "plan-1", "name": "update_plan", "arguments": payload}],
            coordinator=self.coordinator,
            emitter=AsyncMock(),
        )
        return json.loads(result.tool_responses["plan-1"])

    async def test_答复步骤漏掉已失败步骤时给出可操作提示(self):
        """复现验收场景：模型把答复改挂到新增的恢复步骤上。"""
        response = await self._submit(_recovery_plan(answer_depends_on=["search"]))

        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["reason"], "uncovered_execution_branch")
        self.assertEqual(response["hint"], _UNCOVERED_HINT)

    async def test_答复步骤同时依赖失败步骤与恢复步骤时接受(self):
        response = await self._submit(_recovery_plan(answer_depends_on=["forecast", "search"]))

        self.assertEqual(response["status"], "accepted")
        self.assertIn("web_search", self.coordinator.active_plan_tool_names())

    async def test_恢复步骤挂在失败步骤之后会被接受成一份死计划(self):
        """另一个空目录陷阱：终局约束满足，恢复步骤自己却永远跑不起来。

        依赖已失败的步骤会让恢复步骤直接转 blocked，下一轮工具目录仍为空——
        与被拒的效果一样糟，但服务端不会报错。提示文案因此必须同时禁止这种改法。
        """
        response = await self._submit(_recovery_plan(answer_depends_on=["search"], search_depends_on=["forecast"]))

        self.assertEqual(response["status"], "accepted")
        statuses = {item["id"]: item["status"] for item in self.coordinator.items}
        self.assertEqual(statuses["search"], "blocked")
        self.assertEqual(self.coordinator.active_plan_tool_names(), set())

    async def test_提示同时覆盖两个空目录陷阱(self):
        """两条约束都必须写进提示，否则模型只能靠反复试错。"""
        self.assertIn("including steps that already failed or completed", _UNCOVERED_HINT)
        self.assertIn("Do not make the recovery step itself depend on the failed step", _UNCOVERED_HINT)

    async def test_被拒回执仍带上规范计划供模型对照(self):
        response = await self._submit(_recovery_plan(answer_depends_on=["search"]))

        self.assertEqual([item["id"] for item in response["canonical_plan"]], ["forecast", "walk"])


class UncoveredExecutionToolNamesTests(unittest.TestCase):
    """拒绝日志的诊断字段：只回服务端声明过的工具名。"""

    def test_回覆盖率最高的终局步骤漏掉的工具(self):
        items = _recovery_plan(answer_depends_on=["search"])["items"]

        self.assertEqual(
            uncovered_execution_tool_names(items, allowed_tool_names=_ALLOWED),
            ["weather_forecast"],
        )

    def test_覆盖齐全时为空(self):
        items = _recovery_plan(answer_depends_on=["forecast", "search"])["items"]

        self.assertEqual(uncovered_execution_tool_names(items, allowed_tool_names=_ALLOWED), [])

    def test_未声明的工具名不进入诊断(self):
        """模型自造的工具名不能借诊断日志绕过白名单写进日志。"""
        items = _recovery_plan(answer_depends_on=["search"])["items"]
        items[0]["planned_tools"] = ["totally_made_up_tool"]

        self.assertEqual(uncovered_execution_tool_names(items, allowed_tool_names=_ALLOWED), [])

    def test_没有执行步骤时为空(self):
        items = [_item("walk", kind="answer", depends_on=[], planned_tools=[])]

        self.assertEqual(uncovered_execution_tool_names(items, allowed_tool_names=_ALLOWED), [])


if __name__ == "__main__":
    unittest.main()
