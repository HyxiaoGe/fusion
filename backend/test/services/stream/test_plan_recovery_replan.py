"""计划模式下产品工具失败后，模型必须能重新规划。

此前失败项转为 failed 后不再可绑定，active_plan_tool_names() 返回空集，
工具目录被整个清空——模型既换不了工具，也改不了计划，只能以 incomplete 收口。
但能力契约本来就把 web_search/url_read 作为替代工具公开了，两个机制互相抵消。
"""

import unittest

from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import _filter_tools_for_research_stage


def _coordinator_with(items):
    coordinator = PlanCoordinator(run_id="run-1", mode="on")
    coordinator.items = items
    return coordinator


def _item(item_id, status, planned_tools, kind="search"):
    return {
        "id": item_id,
        "title": item_id,
        "status": status,
        "kind": kind,
        "depends_on": [],
        "planned_tools": planned_tools,
    }


class BlockedToolExecutionTests(unittest.TestCase):
    def test_产品工具失败时判定为受阻(self):
        coordinator = _coordinator_with([
            _item("weather", "failed", ["weather_forecast"]),
            _item("answer", "pending", [], kind="answer"),
        ])
        self.assertTrue(coordinator.has_blocked_tool_execution())

    def test_被跳过或阻塞同样算受阻(self):
        for status in ("skipped", "blocked"):
            with self.subTest(status=status):
                coordinator = _coordinator_with([_item("t", status, ["weather_forecast"])])
                self.assertTrue(coordinator.has_blocked_tool_execution())

    def test_仅有待执行项时不算受阻(self):
        coordinator = _coordinator_with([_item("weather", "pending", ["weather_forecast"])])
        self.assertFalse(coordinator.has_blocked_tool_execution())

    def test_没有工具的项失败不算工具执行受阻(self):
        """纯推理/回答项失败不应触发工具恢复重规划。"""
        coordinator = _coordinator_with([_item("reasoning", "failed", [], kind="reasoning")])
        self.assertFalse(coordinator.has_blocked_tool_execution())

    def test_失败项不再可绑定导致工具目录为空(self):
        """复现被观测到的现象：第三轮 tool_names 为空。"""
        coordinator = _coordinator_with([
            _item("weather", "failed", ["weather_forecast"]),
            _item("answer", "pending", [], kind="answer"),
        ])
        self.assertEqual(coordinator.active_plan_tool_names(), set())


class RecoveryReplanBudgetTests(unittest.TestCase):
    def test_默认允许交还改计划权(self):
        coordinator = _coordinator_with([_item("weather", "failed", ["weather_forecast"])])
        self.assertTrue(coordinator.can_attempt_recovery_replan())

    def test_模型原样重交计划后不再交还(self):
        """no_change 说明模型拿不出新方案，再给也没意义——这是无进展检测，
        不是用固定次数压制正常恢复。"""
        coordinator = _coordinator_with([_item("weather", "failed", ["weather_forecast"])])
        coordinator.consecutive_no_progress_updates = 1
        self.assertFalse(coordinator.can_attempt_recovery_replan())

    def test_总修订次数用尽后不再交还(self):
        coordinator = _coordinator_with([_item("weather", "failed", ["weather_forecast"])])
        coordinator.valid_update_count = coordinator.max_valid_updates
        self.assertFalse(coordinator.can_attempt_recovery_replan())

    def test_修订次数未用尽时仍允许(self):
        coordinator = _coordinator_with([_item("weather", "failed", ["weather_forecast"])])
        coordinator.valid_update_count = 1
        self.assertTrue(coordinator.can_attempt_recovery_replan())


class DriverToolGateTests(unittest.TestCase):
    """驱动循环的工具目录裁剪：卡死时必须交还 update_plan。"""

    @staticmethod
    def _call_kwargs():
        return {
            "tools": [
                {"type": "function", "function": {"name": name, "parameters": {}}}
                for name in ("weather_forecast", "web_search", "url_read", "update_plan")
            ]
        }

    @staticmethod
    def _names(call_kwargs):
        return [tool["function"]["name"] for tool in call_kwargs.get("tools") or []]

    def test_卡死时目录收敛为改计划工具(self):
        coordinator = _coordinator_with([
            _item("weather", "failed", ["weather_forecast"]),
            _item("answer", "pending", [], kind="answer"),
        ])
        self.assertEqual(coordinator.active_plan_tool_names(), set())
        self.assertTrue(coordinator.has_blocked_tool_execution())
        self.assertTrue(coordinator.can_attempt_recovery_replan())

        filtered = _filter_tools_for_research_stage(
            self._call_kwargs(),
            allowed_tool_names=frozenset({"update_plan"}),
        )
        self.assertEqual(self._names(filtered), ["update_plan"])

    def test_模型加入恢复步骤后可执行联网工具(self):
        """重规划的目的：下一轮能真正用上契约already公开的替代工具。"""
        coordinator = _coordinator_with([
            _item("weather", "failed", ["weather_forecast"]),
            _item("recover", "pending", ["web_search"]),
            _item("answer", "pending", [], kind="answer"),
        ])
        self.assertEqual(coordinator.active_plan_tool_names(), {"web_search"})

        filtered = _filter_tools_for_research_stage(
            self._call_kwargs(),
            allowed_tool_names=frozenset(coordinator.active_plan_tool_names()),
        )
        self.assertEqual(self._names(filtered), ["web_search"])

    def test_未卡死时不触发恢复分支(self):
        coordinator = _coordinator_with([_item("weather", "pending", ["weather_forecast"])])
        self.assertTrue(coordinator.active_plan_tool_names())
        self.assertFalse(coordinator.has_blocked_tool_execution())


if __name__ == "__main__":
    unittest.main()
