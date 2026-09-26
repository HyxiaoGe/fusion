"""固定语义案例只核对模型轮次约束是否覆盖；实际输出需另做模型验收。"""

import json
import unittest
from pathlib import Path

from app.ai.prompts.product_results import build_product_result_round_prompt
from app.services.stream.product_answer_validator import validate_product_answer

_CORPUS_PATH = Path(__file__).parents[2] / "fixtures" / "product_answer_semantic_acceptance.json"


class ProductAnswerSemanticAcceptanceCorpusTests(unittest.TestCase):
    def test_fixed_cases_have_model_prompt_boundaries(self):
        corpus = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
        cases = corpus["cases"]
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["user_text"])
                self.assertTrue(case["forbidden_examples"])
                self.assertTrue(case["expected_boundary"])
                prompt = build_product_result_round_prompt(case["content_blocks"])
                for fragment in case["prompt_fragments"]:
                    self.assertIn(fragment, prompt)
                # 这些句子只作为真实模型验收的反例；本地校验通过不代表语义正确。
                for example in case["forbidden_examples"]:
                    with self.subTest(example=example):
                        self.assertTrue(validate_product_answer(example, case["content_blocks"]).is_valid)


if __name__ == "__main__":
    unittest.main()
