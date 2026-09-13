"""计划模式下产品工具失败后的恢复重规划。

全程走真实链路：apply_model_update 建立与修订计划、mark_tools_started /
mark_tool_results 推进状态，再用驱动循环的过滤函数断言工具目录。手工设置
items 会绕过计划校验（例如 uncovered_execution_branch），掩盖真实行为。
"""

import unittest

from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import _filter_tools_for_research_stage

_ALLOWED = frozenset({"weather_forecast", "web_search", "url_read"})


def _call_kwargs():
    return {
        "tools": [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in ("weather_forecast", "web_search", "url_read", "update_plan")
        ]
    }


def _offered(coordinator):
    """复刻驱动循环在计划模式下的目录裁剪，返回模型这一轮实际看到的工具。"""
    active = coordinator.active_plan_tool_names()
    if not active and coordinator.claim_recovery_replan():
        allowed = frozenset({"update_plan"})
    else:
        allowed = frozenset(active)
    filtered = _filter_tools_for_research_stage(_call_kwargs(), allowed_tool_names=allowed)
    return [tool["function"]["name"] for tool in filtered.get("tools") or []]


def _coordinator():
    return PlanCoordinator(run_id="run-1", mode="on", allowed_tool_names=_ALLOWED)


def _initial_plan():
    return {
        "reason": "先查天气再回答",
        "items": [
            {"id": "w", "title": "查天气", "status": "pending", "kind": "search",
             "depends_on": [], "planned_tools": ["weather_forecast"]},
            {"id": "a", "title": "回答", "status": "pending", "kind": "answer",
             "depends_on": ["w"], "planned_tools": []},
        ],
    }


def _recovery_plan():
    return {
        "reason": "高德失败，改用联网搜索",
        "items": [
            {"id": "w", "title": "查天气", "status": "failed", "kind": "search",
             "depends_on": [], "planned_tools": ["weather_forecast"]},
            {"id": "s", "title": "搜索天气", "status": "pending", "kind": "search",
             "depends_on": [], "planned_tools": ["web_search"]},
            {"id": "a", "title": "回答", "status": "pending", "kind": "answer",
             "depends_on": ["w", "s"], "planned_tools": []},
        ],
    }


class PlanRecoveryEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = _coordinator()
        self.assertTrue(self.coordinator.apply_model_update(_initial_plan()).accepted)

    def _fail_weather(self):
        self.coordinator.mark_tools_started(["w"])
        self.coordinator.mark_tool_results({"w": "failed"})

    def test_失败前照常提供计划内工具(self):
        self.assertEqual(_offered(self.coordinator), ["weather_forecast"])

    def test_失败后本轮交还改计划权(self):
        self._fail_weather()
        self.assertEqual(_offered(self.coordinator), ["update_plan"])

    def test_加入恢复项后下一轮提供联网工具(self):
        self._fail_weather()
        _offered(self.coordinator)
        self.assertTrue(self.coordinator.apply_model_update(_recovery_plan()).accepted)
        self.assertEqual(_offered(self.coordinator), ["web_search"])

    def test_恢复成功后不再因旧失败强制重规划(self):
        """失败项必须永久保留 failed，不能因此把模型反复推回改计划。"""
        self._fail_weather()
        _offered(self.coordinator)
        self.coordinator.apply_model_update(_recovery_plan())
        _offered(self.coordinator)
        self.coordinator.mark_tools_started(["s"])
        self.coordinator.mark_tool_results({"s": "completed"})

        self.assertEqual(self.coordinator.active_plan_tool_names(), set())
        self.assertEqual(_offered(self.coordinator), [])

    def test_同一失败只领取一次恢复(self):
        self._fail_weather()
        self.assertTrue(self.coordinator.claim_recovery_replan())
        self.assertFalse(self.coordinator.claim_recovery_replan())

    def test_恢复步骤自身失败可再次领取(self):
        """新的失败项是新的未处理失败，应当允许再想办法。"""
        self._fail_weather()
        _offered(self.coordinator)
        self.coordinator.apply_model_update(_recovery_plan())
        _offered(self.coordinator)
        self.coordinator.mark_tools_started(["s"])
        self.coordinator.mark_tool_results({"s": "failed"})

        self.assertEqual(_offered(self.coordinator), ["update_plan"])

    def test_原样重交计划后不再交还改计划权(self):
        """no_change 说明模型拿不出新方案，此时应放行收口而非继续要求改计划。"""
        self._fail_weather()
        result = self.coordinator.apply_model_update(_initial_plan())
        self.assertEqual(result.reason, "no_change")
        self.assertFalse(self.coordinator.can_attempt_recovery_replan())
        self.assertFalse(self.coordinator.claim_recovery_replan())

    def test_总修订次数用尽后不再交还(self):
        self._fail_weather()
        self.coordinator.valid_update_count = self.coordinator.max_valid_updates
        self.assertFalse(self.coordinator.claim_recovery_replan())


class BlockedToolItemTests(unittest.TestCase):
    def test_纯回答项失败不算工具执行受阻(self):
        coordinator = _coordinator()
        coordinator.apply_model_update(_initial_plan())
        coordinator.items = [
            {"id": "r", "title": "推理", "status": "failed", "kind": "reasoning",
             "depends_on": [], "planned_tools": []},
        ]
        self.assertEqual(coordinator.blocked_tool_item_ids(), set())

    def test_跳过与阻塞同样计入受阻(self):
        for status in ("skipped", "blocked"):
            with self.subTest(status=status):
                coordinator = _coordinator()
                coordinator.apply_model_update(_initial_plan())
                coordinator.items[0]["status"] = status
                self.assertEqual(coordinator.blocked_tool_item_ids(), {"w"})


if __name__ == "__main__":
    unittest.main()
