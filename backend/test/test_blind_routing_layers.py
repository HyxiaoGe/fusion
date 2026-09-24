"""探针的层级诊断回归；使用受控分类结果，不评分真实盲测集。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.services.stream.run_capability_router import _CandidateRoute
from scripts import blind_routing_probe as probe


def _direct_candidate():
    return _CandidateRoute("direct", "high", ("stable_knowledge_question",), False)


class BlindRoutingLayerTests(unittest.TestCase):
    def _run(self, cases, *args):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "cases.json"
            fixture.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
            with patch.object(probe, "FIXTURE", fixture), redirect_stdout(output):
                self.assertEqual(probe.main(list(args)), 0)
        return output.getvalue()

    @staticmethod
    def _case(**extra):
        return {
            "id": "诊断样本",
            "question": "你觉得我适合做什么？",
            "group": "identity",
            "acceptable_packages": ["direct"],
            **extra,
        }

    def test_hybrid_layer_mismatch_fails_even_when_package_is_correct(self):
        def classifier(**kwargs):
            kwargs["result_callback"]("literal", None)
            return _direct_candidate()

        with (
            patch.object(probe, "has_hybrid_classifier_credentials", return_value=True),
            patch.object(
                probe, "_new_hybrid_classifier_monitor", return_value=probe.HybridClassifierMonitor(classifier)
            ),
        ):
            for args in ((), ("--verbose",)):
                with self.subTest(args=args):
                    output = self._run([self._case(expected_layer="model")], *args)
                    self.assertIn("layer=literal expected_layer=model layer_check=MISS", output)
                    self.assertRegex(output, r"合计\s+0\s+1")

    def test_hybrid_records_each_case_without_reusing_previous_callback(self):
        results = iter(("literal", "model", None))

        def classifier(**kwargs):
            result = next(results)
            if result is not None:
                kwargs["result_callback"](result, None)
            return _direct_candidate()

        cases = [
            self._case(id=str(index), expected_layer=layer) for index, layer in enumerate(("literal", "model", "model"))
        ]
        with (
            patch.object(probe, "has_hybrid_classifier_credentials", return_value=True),
            patch.object(
                probe, "_new_hybrid_classifier_monitor", return_value=probe.HybridClassifierMonitor(classifier)
            ),
        ):
            output = self._run(cases, "--verbose")
        self.assertIn("layer=literal expected_layer=literal layer_check=OK", output)
        self.assertIn("layer=model expected_layer=model layer_check=OK", output)
        self.assertIn("layer=unknown expected_layer=model layer_check=MISS", output)
        self.assertRegex(output, r"合计\s+2\s+3")

    def test_missing_expected_layer_preserves_package_only_scoring_and_output(self):
        with patch.object(probe, "RulesClassifierMonitor") as monitor:
            output = self._run([self._case(question="你好呀")], "--classifier", "rules", "--verbose")
        monitor.assert_not_called()
        # 规则路径不再做语义预选，任何原句都落到 clarification_only 的 fail-closed 落点。
        self.assertIn("MISS", output)
        self.assertNotIn("layer=", output)
        self.assertRegex(output, r"合计\s+0\s+1")

    def test_layer_match_does_not_hide_incorrect_package(self):
        output = self._run(
            [self._case(question="你好呀", expected_layer="model", acceptable_packages=["verified_web"])],
            "--classifier",
            "rules",
            "--verbose",
        )
        self.assertIn("MISS", output)
        self.assertIn("layer_check=OK", output)
        self.assertRegex(output, r"合计\s+0\s+1")
