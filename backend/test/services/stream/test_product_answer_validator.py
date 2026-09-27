import unittest

from app.schemas.chat import PlaceResult, PlaceResultsBlock
from app.services.stream.product_answer_validator import validate_product_answer


def _place_block():
    return PlaceResultsBlock(
        type="place_results",
        schema_version=1,
        provider="amap",
        query="咖啡馆",
        near="深圳南山科技园",
        status="success",
        result_count=1,
        places=[PlaceResult(name="蓝马咖啡(桑达科技大厦店)", rating=4.5, reference_cost_yuan=22)],
        limitations=["不包含实时排队或空位信息", "参考消费不代表人均或实时价格"],
    )


def _flight_block():
    return {
        "type": "flight_results",
        "status": "success",
        "flights": [
            {
                "flight_no": "MU6670",
                "departure": {"station_name": "宝安国际机场", "scheduled_at": "2026-10-10T21:40:00+08:00"},
                "arrival": {"station_name": "大兴国际机场", "scheduled_at": "2026-10-11T00:50:00+08:00"},
            }
        ],
    }


class ProductAnswerValidatorTests(unittest.TestCase):
    def test_accepts_natural_language_answer_with_product_result(self):
        result = validate_product_answer("蓝马咖啡评分 4.5，可以先打电话确认有没有位子。", [_place_block()])

        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason_code, "ok")

    def test_rejects_empty_or_symbol_only_answer(self):
        for answer in ("", "   ", "……", None):
            with self.subTest(answer=answer):
                self.assertEqual(validate_product_answer(answer, [_place_block()]).reason_code, "empty_answer")

    def test_rejects_markdown_table(self):
        answer = "| 店名 | 评分 |\n| --- | --- |\n| 蓝马咖啡 | 4.5 |"

        self.assertEqual(validate_product_answer(answer, [_place_block()]).reason_code, "unsupported_format")

    def test_rejects_answer_without_product_result(self):
        result = validate_product_answer("蓝马咖啡评分 4.5。", [{"type": "text", "content": "x"}])

        self.assertEqual(result.reason_code, "missing_product_result")

    def test_does_not_parse_answer_wording_against_result_fields(self):
        # 真实验收中被正则误判后兜底的措辞；回答内容边界交给 limitations 与提示词约束。
        cases = [
            ([_place_block()], "推荐先打电话确认有没有位子。"),
            ([_place_block()], "想坐久一点，优先选择营业时间更长的一家。"),
            ([_place_block()], "这几家咖啡的评分都在 3 分以上。"),
            ([_flight_block()], "18:00 以后起飞的晚班可以看 MU6670。"),
            ([_flight_block()], "从深圳宝安国际机场出发，到北京大兴国际机场。"),
        ]
        for blocks, answer in cases:
            with self.subTest(answer=answer):
                self.assertTrue(validate_product_answer(answer, blocks).is_valid)


if __name__ == "__main__":
    unittest.main()
