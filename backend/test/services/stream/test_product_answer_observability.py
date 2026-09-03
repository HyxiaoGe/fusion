"""产品结果回答校验观测记录的字段契约（issue #25）。"""

import json
import unittest

from app.services.stream.product_answer_observability import (
    build_product_answer_observation,
    resolve_reason_category,
)


class ProductAnswerObservabilityTests(unittest.TestCase):
    def test_observation_records_repair_counterfactual_while_disabled(self):
        observation = build_product_answer_observation(
            reason_code="unsupported_claim",
            repair_enabled=False,
            repair_available=True,
            repair_reason_code="unsupported_claim",
            product_result_types=["route_results", "route_results"],
        )

        self.assertFalse(observation["is_valid"])
        self.assertFalse(observation["repair_enabled"])
        self.assertTrue(observation["repair_available"])
        self.assertFalse(observation["repair_applied"])
        self.assertEqual(observation["reason_category"], "risk_term")
        self.assertEqual(observation["product_result_types"], ["route_results"])

    def test_repair_applied_only_when_enabled_and_available(self):
        applied = build_product_answer_observation(
            reason_code="numeric_mismatch",
            repair_enabled=True,
            repair_available=True,
            repair_reason_code=None,
            product_result_types=["weather_results"],
        )
        unavailable = build_product_answer_observation(
            reason_code="numeric_mismatch",
            repair_enabled=True,
            repair_available=False,
            repair_reason_code="numeric_mismatch",
            product_result_types=["weather_results"],
        )

        self.assertTrue(applied["repair_applied"])
        self.assertFalse(unavailable["repair_applied"])

    def test_observation_fields_are_low_cardinality_and_carry_no_free_text(self):
        observation = build_product_answer_observation(
            reason_code="ok",
            repair_enabled=False,
            repair_available=False,
            repair_reason_code=None,
            product_result_types=["flight_results"],
        )

        self.assertTrue(observation["is_valid"])
        self.assertEqual(observation["reason_category"], "valid")
        for value in observation.values():
            self.assertIsInstance(value, (bool, str, list))
        # 序列化后必须仍然只有固定分类，不含任何模型或用户正文。
        self.assertEqual(
            set(observation),
            {
                "reason_code",
                "reason_category",
                "is_valid",
                "repair_enabled",
                "repair_available",
                "repair_applied",
                "repair_reason_code",
                "product_result_types",
            },
        )
        json.dumps(observation, ensure_ascii=False)

    def test_unknown_reason_codes_fall_back_to_a_stable_category(self):
        self.assertEqual(resolve_reason_category("weather_scope_mismatch"), "weather")
        self.assertEqual(resolve_reason_category("brand_new_code"), "other")
