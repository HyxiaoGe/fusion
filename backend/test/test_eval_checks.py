import asyncio
import unittest

from app.evals.cases import EvalCase
from app.evals.checks import JudgeVerdict, evaluate, overall_status


def _case(checks, **extra):
    return EvalCase.model_validate(
        {
            "id": "sample-case",
            "category": "sample",
            "source": "unit test",
            "message": "后天杭州会下雨吗",
            "checks": checks,
            **extra,
        }
    )


def _snapshot(**overrides):
    snapshot = {
        "status": "completed",
        "mode": "agent",
        "skills": [],
        "tool_calls": [
            {
                "tool": "weather_forecast",
                "status": "success",
                "arguments": {"location": "杭州市", "requested_date": "2026-10-06"},
            },
        ],
        "answer_blocks": ["weather_results", "text"],
        "answer_text": "后天杭州小雨。",
    }
    snapshot.update(overrides)
    return snapshot


def _run(case, snapshot, judge=None):
    return asyncio.run(evaluate(case, snapshot, judge))


def _by_type(outcomes):
    return {item.type: item for item in outcomes}


class RunStatusTests(unittest.TestCase):
    def test_completed_run_is_required_by_default(self):
        outcomes = _run(_case([{"type": "tool_called", "tool": "weather_forecast"}]), _snapshot(status="incomplete"))

        self.assertEqual(_by_type(outcomes)["run_status"].status, "failed")
        self.assertEqual(overall_status(outcomes), "failed")

    def test_case_can_accept_other_terminal_status(self):
        outcomes = _run(
            _case([{"type": "run_status", "any_of": ["completed", "limit_reached"]}]),
            _snapshot(status="limit_reached"),
        )

        self.assertEqual([item.type for item in outcomes], ["run_status"])
        self.assertEqual(overall_status(outcomes), "passed")


class DeterministicCheckTests(unittest.TestCase):
    def test_removed_check_types_are_rejected(self):
        for check_type in ("first_package", "escalation"):
            with self.subTest(check_type=check_type), self.assertRaises(ValueError):
                _case([{"type": check_type, "any_of": ["weather"]}])

    def test_tool_called_counts_and_upper_bound(self):
        two_calls = _snapshot(tool_calls=_snapshot()["tool_calls"] * 2)

        within = _run(_case([{"type": "tool_called", "tool": "weather_forecast", "max_count": 2}]), two_calls)
        over = _run(_case([{"type": "tool_called", "tool": "weather_forecast", "max_count": 1}]), two_calls)
        missing = _run(_case([{"type": "tool_called", "tool": "search_trains"}]), two_calls)

        self.assertEqual(_by_type(within)["tool_called"].status, "passed")
        self.assertEqual(_by_type(over)["tool_called"].status, "failed")
        self.assertEqual(_by_type(missing)["tool_called"].status, "failed")

    def test_tool_not_called(self):
        outcomes = _run(_case([{"type": "tool_not_called", "tool": "weather_forecast"}]), _snapshot())

        self.assertEqual(_by_type(outcomes)["tool_not_called"].status, "failed")

    def test_tool_arg_matches_any_call(self):
        snapshot = _snapshot(
            tool_calls=[
                {"tool": "weather_forecast", "arguments": {"location": "上海"}},
                {"tool": "weather_forecast", "arguments": {"location": "杭州市"}},
            ]
        )

        contains = _run(
            _case([{"type": "tool_arg", "tool": "weather_forecast", "field": "location", "contains": "杭州"}]), snapshot
        )
        equals = _run(
            _case([{"type": "tool_arg", "tool": "weather_forecast", "field": "location", "equals": "杭州"}]), snapshot
        )
        any_of = _run(
            _case(
                [{"type": "tool_arg", "tool": "weather_forecast", "field": "location", "any_of": ["杭州市", "杭州"]}]
            ),
            snapshot,
        )

        self.assertEqual(_by_type(contains)["tool_arg"].status, "passed")
        self.assertEqual(_by_type(equals)["tool_arg"].status, "failed")
        self.assertIn("上海", _by_type(equals)["tool_arg"].detail)
        self.assertEqual(_by_type(any_of)["tool_arg"].status, "passed")

    def test_tool_arg_supports_nested_paths_and_missing_tool(self):
        snapshot = _snapshot(tool_calls=[{"tool": "route_compare", "arguments": {"origin": {"city": "上海"}}}])

        nested = _run(
            _case([{"type": "tool_arg", "tool": "route_compare", "field": "origin.city", "equals": "上海"}]), snapshot
        )
        absent = _run(
            _case([{"type": "tool_arg", "tool": "search_trains", "field": "origin", "equals": "武汉"}]), snapshot
        )

        self.assertEqual(_by_type(nested)["tool_arg"].status, "passed")
        self.assertIn("未被调用", _by_type(absent)["tool_arg"].detail)

    def test_skills(self):
        loaded = _snapshot(skills=["trip-planning"])

        none_expected = _run(_case([{"type": "skills", "expect": "none"}]), loaded)
        named = _run(_case([{"type": "skills", "expect": "loaded", "names": ["trip-planning"]}]), loaded)
        wrong_name = _run(_case([{"type": "skills", "expect": "loaded", "names": ["web-research"]}]), loaded)
        nothing_loaded = _run(_case([{"type": "skills", "expect": "loaded"}]), _snapshot())

        self.assertEqual(_by_type(none_expected)["skills"].status, "failed")
        self.assertEqual(_by_type(named)["skills"].status, "passed")
        self.assertEqual(_by_type(wrong_name)["skills"].status, "failed")
        self.assertEqual(_by_type(nothing_loaded)["skills"].status, "failed")

    def test_answer_block_presence_and_absence(self):
        present = _run(
            _case([{"type": "answer_block", "block_type": "document"}]), _snapshot(answer_blocks=["document"])
        )
        absent = _run(_case([{"type": "answer_block", "block_type": "document", "present": False}]), _snapshot())
        unexpected = _run(
            _case([{"type": "answer_block", "block_type": "document", "present": False}]),
            _snapshot(answer_blocks=["document"]),
        )

        self.assertEqual(_by_type(present)["answer_block"].status, "passed")
        self.assertEqual(_by_type(absent)["answer_block"].status, "passed")
        self.assertEqual(_by_type(unexpected)["answer_block"].status, "failed")


class JudgeCheckTests(unittest.TestCase):
    rubric = [{"type": "judge", "rubric": "回答说明了后天是否下雨，且与工具结果一致。"}]

    def test_judge_verdict_is_recorded(self):
        async def judge(check, case, snapshot):
            return JudgeVerdict(passed=False, reason="温度与工具结果不一致")

        outcome = _by_type(_run(_case(self.rubric), _snapshot(), judge))["judge"]

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.detail, "温度与工具结果不一致")

    def test_judge_reads_wider_snapshot_when_given(self):
        seen = []

        async def judge(check, case, snapshot):
            seen.append(snapshot["tool_calls"][0]["result"])
            return JudgeVerdict(passed=True, reason="ok")

        stored = _snapshot()
        wide = _snapshot(tool_calls=[{"tool": "weather_forecast", "arguments": {}, "result": {"full": True}}])

        outcomes = asyncio.run(evaluate(_case(self.rubric), stored, judge, judge_snapshot=wide))

        self.assertEqual(seen, [{"full": True}])
        self.assertEqual(_by_type(outcomes)["judge"].status, "passed")

    def test_judge_failure_is_error_not_model_failure(self):
        async def judge(check, case, snapshot):
            raise TimeoutError("judge timed out")

        outcomes = _run(_case(self.rubric), _snapshot(), judge)

        self.assertEqual(_by_type(outcomes)["judge"].status, "error")
        self.assertEqual(overall_status(outcomes), "error")

    def test_missing_judge_is_error(self):
        outcomes = _run(_case(self.rubric), _snapshot(), None)

        self.assertEqual(_by_type(outcomes)["judge"].status, "error")

    def test_empty_answer_fails_without_calling_judge(self):
        calls = []

        async def judge(check, case, snapshot):
            calls.append(1)
            return JudgeVerdict(passed=True, reason="ok")

        outcome = _by_type(_run(_case(self.rubric), _snapshot(answer_text="  "), judge))["judge"]

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(calls, [])

    def test_failed_check_outranks_error(self):
        async def judge(check, case, snapshot):
            raise RuntimeError("down")

        outcomes = _run(_case([*self.rubric, {"type": "tool_called", "tool": "search_trains"}]), _snapshot(), judge)

        self.assertEqual(overall_status(outcomes), "failed")


if __name__ == "__main__":
    unittest.main()
