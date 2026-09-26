"""能力包计划门禁的单元覆盖；由 CI 的 unittest discover 收集。"""

import unittest

from app.services.stream.agent_plan_tool_policy import resolve_product_package_plan_policy
from app.utils.run_capability_contract import CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES


class PackageDrivenPlanToolPolicyTests(unittest.TestCase):
    def test_产品包按分类结果派生门禁(self):
        cases = (
            ("weather", {"weather_forecast": 1}),
            ("place_discovery", {"local_place_search": 1}),
            ("mobility_route", {"route_compare": 1}),
            ("flight", {"search_flights": 1}),
            ("train", {"search_trains": 1}),
            ("travel_air_rail", {"search_flights": 1, "search_trains": 1}),
        )
        for package_id, required in cases:
            with self.subTest(package_id=package_id):
                announced = list(CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES[package_id])
                policy = resolve_product_package_plan_policy(package_id=package_id, announced_tool_names=announced)
                self.assertIsNotNone(policy)
                self.assertEqual(policy.required_initial_tool_counts, required)
                self.assertEqual(policy.allowed_tool_names, frozenset(announced))
                self.assertEqual(policy.reason, f"capability_package:{package_id}")

    def test_未公开的工具不会被写进门禁(self):
        policy = resolve_product_package_plan_policy(
            package_id="travel_air_rail",
            announced_tool_names=["search_trains"],
        )
        self.assertIsNotNone(policy)
        self.assertEqual(policy.required_initial_tool_counts, {"search_trains": 1})
        self.assertEqual(policy.allowed_tool_names, frozenset({"search_trains"}))

    def test_必需工具一个都没公开时不设门禁(self):
        self.assertIsNone(resolve_product_package_plan_policy(package_id="weather", announced_tool_names=[]))

    def test_非产品包不受影响(self):
        for package_id in ("direct", "transform", "fresh_web", "clarification_only"):
            with self.subTest(package_id=package_id):
                self.assertIsNone(resolve_product_package_plan_policy(package_id=package_id, announced_tool_names=[]))

    def test_跨城与混合行程的包策略不擅自选择主工具(self):
        for package_id in ("mobility_intercity", "mixed_itinerary"):
            with self.subTest(package_id=package_id):
                announced = list(CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES[package_id])
                self.assertIsNone(
                    resolve_product_package_plan_policy(package_id=package_id, announced_tool_names=announced)
                )
