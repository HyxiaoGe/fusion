import unittest

from app.schemas.chat import PlaceResult, PlaceResultsBlock
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.product_structured_answers import PRODUCT_STRUCTURED_ANSWERS


class ProductAnswerRewriteTests(unittest.TestCase):
    def test_visible_answer_preserves_brand_entities_without_result_cards(self):
        for answer in (
            "高德置地广场是你提到的目的地。",
            "FlyAI Learning 是用户提供的机构名称。",
            "The route from FlyAI Plaza is returned by FlyAI.",
            "飞猪旅行的页面标题保留在用户提供的资料中。",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(PRODUCT_STRUCTURED_ANSWERS.rewrite_answer(answer, AgentLoopState()), answer)

    def test_visible_answer_preserves_structured_brand_entities_and_attribution(self):
        entity_block = PlaceResultsBlock(
            type="place_results",
            schema_version=1,
            provider="amap",
            query="商场",
            status="success",
            result_count=1,
            places=[PlaceResult(name="高德置地广场")],
        )
        answer = "高德置地广场是候选地点。高德返回了路线结果。"
        state = AgentLoopState(content_blocks=[entity_block])
        self.assertEqual(PRODUCT_STRUCTURED_ANSWERS.rewrite_answer(answer, state), answer)

    def test_visible_answer_still_sanitizes_internal_tool_identifiers(self):
        self.assertEqual(
            PRODUCT_STRUCTURED_ANSWERS.rewrite_answer("local_place_search 返回高德置地广场。", AgentLoopState()),
            "地点搜索返回高德置地广场。",
        )
