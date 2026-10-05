import unittest

from app.services.agent.events import AgentPlanItem
from app.services.agent.plan_coordinator import PlanCoordinator


def _plan(*steps: dict) -> dict:
    return {"explanation": "按顺序查询", "plan": list(steps)}


class PlanCoordinatorTest(unittest.TestCase):
    def test_plan_mode_off_rejects_update(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="off")

        result = coordinator.apply_model_update(_plan({"step": "查天气", "status": "pending"}))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "plan_mode_off")
        self.assertFalse(coordinator.has_valid_model_plan)

    def test_accepts_loose_plan_without_structure_rules(self):
        """单步、无回答步骤、多工具同一步都照单全收：计划只用于展示。"""

        coordinator = PlanCoordinator(run_id="run-1", mode="on")

        result = coordinator.apply_model_update(
            _plan({"step": "同时查天气和路线", "status": "pending", "planned_tools": ["weather", "route"]})
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.snapshot["revision"], 1)
        self.assertEqual(result.snapshot["source"], "model")
        item = result.snapshot["items"][0]
        self.assertEqual(item["id"], "step-1")
        self.assertEqual(item["planned_tools"], ["weather", "route"])
        self.assertEqual(item["phase_title"], "同时查天气和路线")
        # 快照字段必须与前端消费的事件协议一致。
        for snapshot_item in result.snapshot["items"]:
            AgentPlanItem.model_validate(snapshot_item)

    def test_unrecognizable_payload_is_rejected_without_side_effects(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")

        result = coordinator.apply_model_update({"plan": [{"status": "pending"}]})

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_plan_structure")
        self.assertEqual(coordinator.revision, 0)

    def test_model_status_aliases_are_accepted(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")

        coordinator.apply_model_update(
            _plan(
                {"id": "a", "step": "查询", "status": "in_progress"},
                {"id": "b", "step": "回答", "status": "completed"},
            )
        )

        self.assertEqual([item["status"] for item in coordinator.items], ["running", "completed"])

    def test_tool_progress_maps_calls_to_steps_best_effort(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        coordinator.apply_model_update(
            _plan(
                {"id": "w", "step": "查天气", "status": "pending", "planned_tools": ["weather"]},
                {"id": "r", "step": "查路线", "status": "pending", "planned_tools": ["route"]},
            )
        )

        mapped = coordinator.plan_item_ids_for_tools(["route", "weather", "web_search"])
        self.assertEqual(mapped, ["r", "w", None])

        started = coordinator.mark_tools_started(["r"])
        self.assertEqual(started["items"][1]["status"], "running")
        finished = coordinator.mark_tool_results({"r": "completed", "w": "failed"})
        self.assertEqual([item["status"] for item in finished["items"]], ["failed", "completed"])

    def test_server_terminal_status_survives_model_revision(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        coordinator.apply_model_update(
            _plan({"id": "w", "step": "查天气", "status": "pending", "planned_tools": ["weather"]})
        )
        coordinator.mark_tool_results({"w": "completed"})

        coordinator.apply_model_update(
            _plan(
                {"id": "w", "step": "查天气", "status": "pending"},
                {"id": "a", "step": "回答", "status": "pending"},
            )
        )

        self.assertEqual(coordinator.items[0]["status"], "completed")
        self.assertEqual(coordinator.items[0]["planned_tools"], ["weather"])

    def test_terminalize_with_answer_completes_tool_free_steps(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        coordinator.apply_model_update(
            _plan(
                {"id": "w", "step": "查天气", "status": "pending", "planned_tools": ["weather"]},
                {"id": "a", "step": "回答", "status": "pending"},
            )
        )

        snapshot = coordinator.terminalize("stop", has_final_answer=True)

        self.assertEqual([item["status"] for item in snapshot["items"]], ["skipped", "completed"])
        self.assertIsNone(coordinator.terminalize("stop", has_final_answer=True))
        self.assertFalse(coordinator.apply_model_update(_plan({"step": "x", "status": "pending"})).accepted)

    def test_terminalize_on_limit_blocks_unfinished_steps(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        coordinator.apply_model_update(_plan({"id": "w", "step": "查天气", "status": "pending"}))

        snapshot = coordinator.terminalize("limit_reached")

        self.assertEqual(snapshot["items"][0]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
