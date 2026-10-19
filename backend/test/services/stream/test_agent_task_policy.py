import unittest

from app.services.stream.agent_task_policy import resolve_agent_task_policy, restore_agent_task_policy


class AgentTaskPolicyTests(unittest.TestCase):
    def test_plan_mode_follows_request(self):
        self.assertEqual(resolve_agent_task_policy(options={"plan_mode": "off"}).plan_mode, "off")
        self.assertEqual(resolve_agent_task_policy(options={"plan_mode": "on"}).plan_mode, "on")

    def test_removed_deep_research_task_mode_is_ignored(self):
        """旧客户端仍可能带 task_mode=deep_research：按普通请求处理，不再强制开计划。"""

        policy = resolve_agent_task_policy(options={"task_mode": "deep_research", "plan_mode": "off"})

        self.assertEqual(policy.plan_mode, "off")
        self.assertEqual(policy.apply_to_options({"task_mode": "deep_research"})["plan_mode"], "off")

    def test_restore_from_legacy_deep_research_run_keeps_recorded_plan_mode(self):
        policy = restore_agent_task_policy(
            {"task_mode": "deep_research", "plan_mode": "on", "network_profile": "deep_research"}
        )

        self.assertEqual(policy.plan_mode, "on")
