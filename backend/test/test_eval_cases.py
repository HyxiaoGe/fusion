import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.evals.cases import EvalCase, load_suite


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


class RepositorySuiteTests(unittest.TestCase):
    def test_repository_cases_all_validate(self):
        suite = load_suite()

        self.assertGreater(len(suite.cases), 0)
        self.assertEqual(len({case.id for case in suite.cases}), len(suite.cases))
        self.assertEqual(len(suite.sha256), 64)

    def test_every_case_records_where_it_came_from(self):
        for case in load_suite().cases:
            self.assertTrue(case.source.strip(), case.id)


class CaseValidationTests(unittest.TestCase):
    def _case(self, **overrides):
        payload = {
            "id": "sample-case",
            "category": "sample",
            "source": "unit test",
            "message": "你好",
            "checks": [{"type": "escalation", "expect": "none"}],
        }
        payload.update(overrides)
        return EvalCase.model_validate(payload)

    def test_unknown_check_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._case(checks=[{"type": "answer_contains", "text": "杭州"}])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._case(checks=[{"type": "tool_called", "tool": "web_search", "min": 1}])

    def test_tool_arg_needs_exactly_one_condition(self):
        with self.assertRaises(ValidationError):
            self._case(checks=[{"type": "tool_arg", "tool": "weather_forecast", "field": "location"}])
        with self.assertRaises(ValidationError):
            self._case(
                checks=[
                    {
                        "type": "tool_arg",
                        "tool": "weather_forecast",
                        "field": "location",
                        "equals": "杭州",
                        "contains": "杭",
                    }
                ]
            )

    def test_tool_called_bounds_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self._case(checks=[{"type": "tool_called", "tool": "web_search", "min_count": 3, "max_count": 1}])

    def test_case_needs_at_least_one_check(self):
        with self.assertRaises(ValidationError):
            self._case(checks=[])

    def test_model_scope(self):
        scoped = self._case(models=["qwen3.8-flash"])

        self.assertTrue(scoped.applies_to("qwen3.8-flash"))
        self.assertFalse(scoped.applies_to("mimo-v2.6-pro"))
        self.assertTrue(self._case().applies_to("mimo-v2.6-pro"))


class SuiteLoadingTests(unittest.TestCase):
    def test_duplicate_ids_across_files_are_rejected(self):
        case = "  - id: same-id\n    source: unit test\n    message: hi\n    checks:\n      - type: escalation\n        expect: none\n"
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "a.yaml", f"category: alpha\ncases:\n{case}")
            _write(Path(tmp), "b.yaml", f"category: beta\ncases:\n{case}")

            with self.assertRaisesRegex(ValueError, "same-id"):
                load_suite(Path(tmp))

    def test_category_comes_from_file(self):
        case = "  - id: only-case\n    source: unit test\n    message: hi\n    checks:\n      - type: escalation\n        expect: none\n"
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "a.yaml", f"category: alpha\ncases:\n{case}")

            suite = load_suite(Path(tmp))

        self.assertEqual(suite.cases[0].category, "alpha")

    def test_select_by_unknown_case_id_fails_loudly(self):
        suite = load_suite()

        with self.assertRaisesRegex(ValueError, "未知用例"):
            suite.select(case_ids={"no-such-case"})

    def test_select_by_category(self):
        suite = load_suite()
        category = suite.cases[0].category

        chosen = suite.select(categories={category})

        self.assertTrue(chosen)
        self.assertTrue(all(case.category == category for case in chosen))

    def test_empty_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                load_suite(Path(tmp))


if __name__ == "__main__":
    unittest.main()
