"""计划执行期间的修订入口，以及它的预算必须收敛。

真实样本 Run fcbfcab7（线上 0efef14）：高德失败 → 恢复计划被接受 → web_search 成功 →
第六轮工具目录为空。模型想读权威原文核对，却拿不到 update_plan——「加一步」这个动作
本身需要 update_plan，而它此前只在出现新失败时才发。模型只能先制造一次失败才拿得回
改计划权，而搜索是成功的。同一形状在三次真实验收里出现了三次。

根因是一层嵌套：预算判断（能改几次）被写进了失败判断（什么时候能改）内部。

本文件的重点不是「门开没开」——那读代码就能看出来——而是**开了之后收不收得住**。
所以下半部分不断言限流机制存在，而是真的一轮轮驱动到门关上，并且走真实控制调用链路
process_plan_control_calls：被拒的修订不消耗协调器的修订预算，它由轮级的
repair_attempt_count 兜底，只驱动 apply_model_update 会漏掉这条边界。
"""

import json
import unittest
from unittest.mock import AsyncMock

from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.agent_loop_driver import resolve_plan_mode_tool_policy
from app.services.stream.plan_control import process_plan_control_calls

_ALLOWED = frozenset({"weather_forecast", "web_search", "url_read"})


def _item(item_id, *, kind, depends_on, planned_tools):
    return {
        "id": item_id,
        "title": f"步骤 {item_id}",
        "status": "pending",
        "kind": kind,
        "depends_on": list(depends_on),
        "planned_tools": list(planned_tools),
    }


def _plan(*, filler=0, read_step=False):
    """检索 + 若干无工具步骤 + 回答；计划总步数保持在服务端要求的 2–6 之内。"""

    items = [_item("search", kind="search", depends_on=[], planned_tools=["web_search"])]
    depends = "search"
    if read_step:
        items.append(_item("official_read", kind="search", depends_on=[depends], planned_tools=["url_read"]))
        depends = "official_read"
    for index in range(filler):
        step_id = f"note{index}"
        items.append(_item(step_id, kind="reasoning", depends_on=[depends], planned_tools=[]))
        depends = step_id
    items.append(_item("answer", kind="answer", depends_on=[depends], planned_tools=[]))
    return {"reason": "先检索再回答", "items": items}


def _coordinator(run_id: str) -> PlanCoordinator:
    coordinator = PlanCoordinator(run_id=run_id, mode="on", allowed_tool_names=_ALLOWED)
    assert coordinator.apply_model_update(_plan()).accepted
    coordinator.mark_tools_started(["search"])
    coordinator.mark_tool_results({"search": "completed"})
    return coordinator


class PlanRevisionEntryTests(unittest.TestCase):
    """执行完毕、无待处理失败时，模型应当能补一步取证，也能直接作答。"""

    def setUp(self):
        self.coordinator = PlanCoordinator(run_id="run-1", mode="on", allowed_tool_names=_ALLOWED)
        self.assertTrue(self.coordinator.apply_model_update(_plan()).accepted)

    def test_执行途中照常锁定执行不开放修订(self):
        """有可执行步骤时行为不变：只给计划内工具，并锁定工具选择。"""
        policy = resolve_plan_mode_tool_policy(self.coordinator)

        self.assertEqual(policy.allowed_tool_names, frozenset({"web_search"}))
        self.assertTrue(policy.require_tool_call)

    def test_搜索成功后拿得到改计划权(self):
        """复现真实样本：此前这里是空目录，模型无法新增取证步骤。"""
        self.coordinator.mark_tools_started(["search"])
        self.coordinator.mark_tool_results({"search": "completed"})

        self.assertEqual(
            resolve_plan_mode_tool_policy(self.coordinator).allowed_tool_names,
            frozenset({"update_plan"}),
        )

    def test_修订入口不得强制否则永远答不了(self):
        """最关键的一条：开放不等于强制。强制会把收口作答这条路堵死。"""
        self.coordinator.mark_tools_started(["search"])
        self.coordinator.mark_tool_results({"search": "completed"})

        policy = resolve_plan_mode_tool_policy(self.coordinator)

        # 两条必须一起断言：旧行为下目录为空、也不强制，单看 require_tool_call 是空转的。
        self.assertIn("update_plan", policy.allowed_tool_names)
        self.assertFalse(policy.require_tool_call)
        self.assertIsNone(policy.preferred_tool_name)

    def test_补一步取证后回到锁定执行(self):
        self.coordinator.mark_tools_started(["search"])
        self.coordinator.mark_tool_results({"search": "completed"})
        self.assertIn(
            "update_plan",
            resolve_plan_mode_tool_policy(self.coordinator).allowed_tool_names,
            "前置条件：模型得先拿得到 update_plan 才谈得上补步骤",
        )

        self.assertTrue(self.coordinator.apply_model_update(_plan(read_step=True)).accepted)

        policy = resolve_plan_mode_tool_policy(self.coordinator)
        self.assertEqual(policy.allowed_tool_names, frozenset({"url_read"}))
        self.assertTrue(policy.require_tool_call)

    def test_计划尚未建立时仍然强制建计划(self):
        empty = PlanCoordinator(run_id="run-empty", mode="on", allowed_tool_names=_ALLOWED)

        policy = resolve_plan_mode_tool_policy(empty)

        self.assertEqual(policy.allowed_tool_names, frozenset({"update_plan"}))
        self.assertTrue(policy.require_tool_call)
        self.assertEqual(policy.preferred_tool_name, "update_plan")


class RevisionBudgetConvergenceTests(unittest.TestCase):
    """修订入口常驻之后，三条预算都必须真的收得住。"""

    def _revision_offered(self, coordinator) -> bool:
        return "update_plan" in resolve_plan_mode_tool_policy(coordinator).allowed_tool_names

    def test_反复做被接受但无意义的修订会撞上总修订上限(self):
        """无进展检测只覆盖「原样重交」，覆盖不了「每次改一点」。这条由总数上限兜底。"""
        coordinator = _coordinator("run-churn")
        self.assertTrue(self._revision_offered(coordinator), "前置条件：修订入口应当是开的")
        rounds = 0

        while self._revision_offered(coordinator) and rounds < 50:
            rounds += 1
            # 每轮都换一份结构不同、实质无意义的计划：只增减不带工具的步骤。
            coordinator.apply_model_update(_plan(filler=rounds % 4))

        self.assertFalse(self._revision_offered(coordinator), "修订入口没有收敛，模型可以无限改计划")
        self.assertLessEqual(rounds, 8, f"收敛太慢：用了 {rounds} 轮")

    def test_原样重交一次就关门(self):
        """no_change 说明模型拿不出新方案，无进展检测应当立刻收口。"""
        coordinator = _coordinator("run-nochange")
        self.assertTrue(self._revision_offered(coordinator), "前置条件：修订入口应当是开的")

        result = coordinator.apply_model_update(
            {"reason": "维持原计划", "items": coordinator.canonical_plan_for_model()}
        )

        self.assertEqual(result.reason, "no_change")
        self.assertFalse(self._revision_offered(coordinator))

    def test_门关上后目录为空且不强制模型可直接作答(self):
        """预算耗尽不是死锁：收口作答的路始终留着。"""
        coordinator = _coordinator("run-exhausted")
        self.assertTrue(self._revision_offered(coordinator), "前置条件：修订入口应当是开的")
        coordinator.apply_model_update({"reason": "维持原计划", "items": coordinator.canonical_plan_for_model()})

        policy = resolve_plan_mode_tool_policy(coordinator)

        self.assertEqual(policy.allowed_tool_names, frozenset())
        self.assertFalse(policy.require_tool_call)


class RejectedRevisionBudgetTests(unittest.IsolatedAsyncioTestCase):
    """被拒的修订不消耗协调器预算，必须由轮级的修复次数兜底。

    只驱动 apply_model_update 会漏掉这条：非法计划连续被拒时
    valid_update_count 与 consecutive_no_progress_updates 都不动，
    修订入口会一直开着。真实链路里限流在 process_plan_control_calls。
    """

    async def test_连续提交非法计划会在有限轮内耗尽修复次数(self):
        coordinator = _coordinator("run-rejected")
        self.assertTrue(
            "update_plan" in resolve_plan_mode_tool_policy(coordinator).allowed_tool_names,
            "前置条件：修订入口应当是开的",
        )
        invalid = {"reason": "结构非法", "items": [_item("only", kind="answer", depends_on=[], planned_tools=[])]}
        exhausted = False

        for _round in range(10):
            result = await process_plan_control_calls(
                tool_calls=[{"id": "plan-1", "name": "update_plan", "arguments": invalid}],
                coordinator=coordinator,
                emitter=AsyncMock(),
            )
            self.assertEqual(json.loads(result.tool_responses["plan-1"])["status"], "rejected")
            if result.repair_exhausted:
                exhausted = True
                break

        self.assertTrue(exhausted, "非法计划被无限重试，轮级修复次数没有兜住")
        self.assertGreater(coordinator.repair_attempt_count, 0)


if __name__ == "__main__":
    unittest.main()
