"""产品结果回答校验观测记录的字段契约（issue #25）。"""

import json
import unittest

from app.services.stream.product_answer_observability import (
    build_product_answer_observation,
    resolve_reason_category,
)


class ProductAnswerObservabilityTests(unittest.TestCase):
    def test_invalid_observation_records_shape_category(self):
        observation = build_product_answer_observation(
            reason_code="unsupported_format",
            product_result_types=["route_results", "route_results"],
        )

        self.assertFalse(observation["is_valid"])
        self.assertEqual(observation["reason_category"], "shape")
        self.assertEqual(observation["product_result_types"], ["route_results"])

    def test_observation_fields_are_low_cardinality_and_carry_no_free_text(self):
        observation = build_product_answer_observation(
            reason_code="ok",
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
                "observation_path",
                "validated",
                "product_tool_attempted",
                "reason_code",
                "reason_category",
                "is_valid",
                "product_result_types",
            },
        )
        json.dumps(observation, ensure_ascii=False)

    def test_unknown_reason_codes_fall_back_to_a_stable_category(self):
        self.assertEqual(resolve_reason_category("unknown_place"), "other")
        self.assertEqual(resolve_reason_category("brand_new_code"), "other")
