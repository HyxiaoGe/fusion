import json
import unittest
from unittest.mock import AsyncMock, Mock

from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.stream.plan_control import process_plan_control_calls


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


class PlanControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_calls_run_without_a_plan(self):
        """计划模式下没交计划也不拦外部工具：计划只做展示。"""

        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        emitter = Mock(plan_snapshot=AsyncMock())

        result = await process_plan_control_calls(
            tool_calls=[_call("c1", "web_search", {"query": "天气"})],
            coordinator=coordinator,
            emitter=emitter,
        )

        self.assertEqual([call["name"] for call in result.external_tool_calls], ["web_search"])
        self.assertEqual(result.tool_responses, {})
        emitter.plan_snapshot.assert_not_awaited()

    async def test_plan_and_tools_in_same_round(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        emitter = Mock(plan_snapshot=AsyncMock())

        result = await process_plan_control_calls(
            tool_calls=[
                _call(
                    "p1",
                    "update_plan",
                    {
                        "plan": [
                            {"id": "s", "step": "搜索资料", "status": "pending", "planned_tools": ["web_search"]},
                            {"id": "a", "step": "整理回答", "status": "pending"},
                        ]
                    },
                ),
                _call("c1", "web_search", {"query": "天气", "_plan_item_id": "s"}),
            ],
            coordinator=coordinator,
            emitter=emitter,
        )

        self.assertEqual(json.loads(result.tool_responses["p1"])["status"], "accepted")
        emitter.plan_snapshot.assert_awaited_once()
        [external] = result.external_tool_calls
        self.assertEqual(external["plan_item_id"], "s")
        self.assertEqual(json.loads(external["arguments"]), {"query": "天气"})
        self.assertEqual(result.plan_item_ids, {"c1": "s"})

    async def test_rejected_plan_does_not_block_other_calls(self):
        coordinator = PlanCoordinator(run_id="run-1", mode="on")
        emitter = Mock(plan_snapshot=AsyncMock())

        result = await process_plan_control_calls(
            tool_calls=[_call("p1", "update_plan", {"plan": "not a list"}), _call("c1", "url_read", {"url": "u"})],
            coordinator=coordinator,
            emitter=emitter,
        )

        self.assertEqual(json.loads(result.tool_responses["p1"])["status"], "rejected")
        self.assertEqual([call["name"] for call in result.external_tool_calls], ["url_read"])
        self.assertNotIn("plan_item_id", result.external_tool_calls[0])


if __name__ == "__main__":
    unittest.main()
