import asyncio
import json
import unittest
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import (
    AgentEvent,
    AgentSession,
    Conversation,
    EvalCaseResult,
    EvalSuiteRun,
    Message,
    ToolCallLog,
    User,
)
from app.evals import runner, store
from app.evals.cases import EvalCase
from app.evals.checks import CheckOutcome
from app.evals.judge import build_judge_messages, parse_verdict
from app.evals.runner import CaseOutcome, _TurnResult, ensure_eval_user, parse_sse_frame
from app.evals.trajectory import (
    JUDGE_TOOL_RESULT_CHAR_LIMIT,
    TOOL_RESULT_CHAR_LIMIT,
    build_snapshot,
    load_snapshot,
)
from app.utils.time import utc_now
from scripts import run_eval_suite

_TABLES = [
    User.__table__,
    Conversation.__table__,
    Message.__table__,
    AgentSession.__table__,
    AgentEvent.__table__,
    ToolCallLog.__table__,
    EvalSuiteRun.__table__,
    EvalCaseResult.__table__,
]


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=_TABLES)
    return sessionmaker(bind=engine, expire_on_commit=False)


class BuildSnapshotTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        args = {
            "session": {"id": "run-1", "conversation_id": "conv-1", "model_id": "qwen3.8-flash", "status": "completed"},
            "events": [
                ("run_started", {"capability_resolution": {"package_id": "agent"}}),
                ("skills_resolved", {"status": "loaded", "skills": [{"skill_id": "web-research"}]}),
                ("run_completed", {"finish_reason": "stop"}),
            ],
            "tool_logs": [
                {"tool_name": "load_skill", "status": "success", "detail": {"payload": {"name": "trip-planning"}}},
                {
                    "tool_name": "weather_forecast",
                    "status": "success",
                    "detail": {"payload": {"location": "杭州"}, "result": {"forecast_days": [{"date": "2026-10-06"}]}},
                },
            ],
            "answer_content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "weather_results", "status": "success"},
                {"type": "text", "text": "后天杭州小雨。"},
            ],
        }
        args.update(overrides)
        return build_snapshot(**args)

    def test_extracts_mode_tools_and_answer(self):
        snapshot = self._snapshot()

        self.assertEqual(snapshot["mode"], "agent")
        self.assertEqual(snapshot["finish_reason"], "stop")
        self.assertEqual(snapshot["skills"], ["web-research", "trip-planning"])
        self.assertEqual([call["tool"] for call in snapshot["tool_calls"]], ["load_skill", "weather_forecast"])
        self.assertEqual(snapshot["tool_calls"][1]["arguments"], {"location": "杭州"})
        self.assertEqual(snapshot["answer_blocks"], ["weather_results", "text"])
        self.assertEqual(snapshot["answer_text"], "后天杭州小雨。")

    def test_large_tool_results_are_truncated_for_storage(self):
        big = {"items": ["x" * 100] * 200}
        snapshot = self._snapshot(
            tool_logs=[{"tool_name": "web_search", "status": "success", "detail": {"payload": {}, "result": big}}]
        )

        result = snapshot["tool_calls"][0]["result"]
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["preview"]), TOOL_RESULT_CHAR_LIMIT)

    def test_judge_snapshot_keeps_full_search_results(self):
        big = {"items": ["x" * 100] * 200}
        snapshot = self._snapshot(
            tool_logs=[{"tool_name": "web_search", "status": "success", "detail": {"payload": {}, "result": big}}],
            result_limit=JUDGE_TOOL_RESULT_CHAR_LIMIT,
        )

        self.assertEqual(snapshot["tool_calls"][0]["result"], big)

    def test_missing_answer_and_resolution_are_tolerated(self):
        snapshot = self._snapshot(events=[("run_started", {"capability_resolution": None})], answer_content=None)

        self.assertIsNone(snapshot["mode"])
        self.assertEqual(snapshot["answer_text"], "")
        self.assertEqual(snapshot["answer_blocks"], [])


class LoadSnapshotTests(unittest.TestCase):
    def test_reads_run_from_ledger_tables(self):
        factory = _session_factory()
        with factory() as db:
            user = User(username="u1")
            db.add(user)
            db.flush()
            conv = Conversation(user_id=user.id, title="t", model_id="qwen3.8-flash")
            db.add(conv)
            db.flush()
            answer = Message(conversation_id=conv.id, role="assistant", content=[{"type": "text", "text": "好的"}])
            db.add(answer)
            db.flush()
            db.add(
                AgentSession(
                    id="run-1",
                    conversation_id=conv.id,
                    message_id=answer.id,
                    user_id=user.id,
                    model_id="qwen3.8-flash",
                    provider="litellm",
                    status="completed",
                )
            )
            db.flush()
            for sequence, (event_type, payload) in enumerate(
                [
                    ("run_started", {"capability_resolution": {"package_id": "agent"}}),
                    ("run_completed", {"finish_reason": "stop"}),
                ],
                start=1,
            ):
                db.add(
                    AgentEvent(
                        event_id=uuid.uuid4(),
                        conversation_id=conv.id,
                        run_id="run-1",
                        sequence=sequence,
                        event_type=event_type,
                        event_ts=utc_now(),
                        payload=payload,
                    )
                )
            db.add(
                ToolCallLog(
                    conversation_id=conv.id,
                    user_id=user.id,
                    tool_name="weather_forecast",
                    status="success",
                    model_id="qwen3.8-flash",
                    provider="litellm",
                    trace_id="run-1",
                    tool_call_id="call-1",
                    step_number=1,
                    extra_metadata={"trajectory_detail": {"payload": {"location": "杭州"}, "result": {"ok": True}}},
                )
            )
            db.commit()

            snapshot = load_snapshot(db, "run-1")
            missing = load_snapshot(db, "no-such-run")

        self.assertEqual(snapshot["mode"], "agent")
        self.assertEqual(snapshot["tool_calls"][0]["arguments"], {"location": "杭州"})
        self.assertEqual(snapshot["answer_text"], "好的")
        self.assertIsNone(missing)


class SseParsingTests(unittest.TestCase):
    def test_captures_run_ids_and_errors(self):
        turn = _TurnResult()
        started = {
            "chunk_type": "agent_event",
            "data": {"type": "run_started", "run_id": "r1", "conversation_id": "c1"},
        }
        error = {"chunk_type": "error", "data": {"message": "upstream 500"}}

        parse_sse_frame(f"id: 1-0\ndata: {json.dumps(started)}", turn)
        parse_sse_frame(f"data: {json.dumps(error)}", turn)
        parse_sse_frame(": keepalive", turn)
        parse_sse_frame("data: [DONE]", turn)

        self.assertEqual((turn.run_id, turn.conversation_id), ("r1", "c1"))
        self.assertEqual(turn.errors, ["upstream 500"])

    def test_first_run_started_wins(self):
        turn = _TurnResult()
        for run_id in ("r1", "r2"):
            frame = {
                "chunk_type": "agent_event",
                "data": {"type": "run_started", "run_id": run_id, "conversation_id": "c"},
            }
            parse_sse_frame(f"data: {json.dumps(frame)}", turn)

        self.assertEqual(turn.run_id, "r1")


def _case_with(**extra):
    return EvalCase.model_validate(
        {
            "id": "sample-case",
            "category": "sample",
            "source": "unit test",
            "message": "后天杭州会下雨吗",
            "checks": [{"type": "tool_called", "tool": "weather_forecast"}],
            **extra,
        }
    )


class RunCaseTests(unittest.TestCase):
    """用假的发送与账本驱动 run_case，覆盖完成、超时、缺 run_id、发送异常四条路径。"""

    def setUp(self):
        self.stopped = []
        self.sent = []

        async def stop(conversation_id):
            self.stopped.append(conversation_id)

        async def wait_terminal(factory, run_id):
            return "completed"

        patches = [
            patch.object(runner, "_stop_generation", stop),
            patch.object(runner, "_wait_terminal", wait_terminal),
            patch.object(
                runner,
                "load_snapshot",
                lambda db, run_id: {
                    "run_id": run_id,
                    "status": "completed",
                    "tool_calls": [{"tool": "weather_forecast", "arguments": {}}],
                },
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _run(self, send, case=None):
        with patch.object(runner, "_send_turn", send):
            return asyncio.run(
                runner.run_case(case or _case_with(), "m", user_id="u", session_factory=nullcontext, judge=None)
            )

    def test_completed_run_is_judged(self):
        async def send(db, turn, **kwargs):
            self.sent.append((kwargs["message"], turn.conversation_id))
            turn.run_id, turn.conversation_id = f"run-{len(self.sent)}", "conv-1"

        outcome = self._run(send, _case_with(setup_turns=["我在杭州"]))

        self.assertEqual(outcome.status, "passed")
        self.assertEqual((outcome.run_id, outcome.conversation_id), ("run-2", "conv-1"))
        self.assertEqual(self.sent, [("我在杭州", None), ("后天杭州会下雨吗", "conv-1")])
        self.assertEqual(self.stopped, [])

    def test_waits_for_generation_task_after_terminal_status(self):
        from app.services import task_manager

        finished = []

        async def send(db, turn, **kwargs):
            async def finalize():
                await asyncio.sleep(0.05)
                finished.append(True)

            turn.run_id, turn.conversation_id = "run-1", "conv-finalize"
            task_manager.register_task("conv-finalize", asyncio.create_task(finalize()), "task-1")

        outcome = self._run(send)

        self.assertEqual(outcome.status, "passed")
        self.assertEqual(finished, [True])

    def test_timeout_stops_generation_and_keeps_trajectory(self):
        async def send(db, turn, **kwargs):
            turn.run_id, turn.conversation_id = "run-slow", "conv-slow"
            await asyncio.sleep(10)

        with patch.object(runner, "CASE_TIMEOUT_SECONDS", 0.05):
            outcome = self._run(send)

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.run_id, "run-slow")
        self.assertEqual(self.stopped, ["conv-slow"])
        self.assertEqual(outcome.snapshot["run_id"], "run-slow")
        self.assertEqual([(c.type, c.status) for c in outcome.checks], [("run_status", "failed")])
        self.assertIn("本轮超过", outcome.checks[0].detail)

    def test_timeout_during_setup_turn_is_labelled(self):
        async def send(db, turn, **kwargs):
            turn.run_id, turn.conversation_id = "run-setup", "conv-setup"
            await asyncio.sleep(10)

        with patch.object(runner, "CASE_TIMEOUT_SECONDS", 0.05):
            outcome = self._run(send, _case_with(setup_turns=["我在杭州"]))

        self.assertIn("前置对话超过", outcome.checks[0].detail)

    def test_missing_run_id_is_error(self):
        async def send(db, turn, **kwargs):
            turn.errors.append("upstream 500")

        outcome = self._run(send)

        self.assertEqual(outcome.status, "error")
        self.assertIn("upstream 500", outcome.error)

    def test_send_exception_is_error(self):
        async def send(db, turn, **kwargs):
            raise RuntimeError("redis down")

        outcome = self._run(send)

        self.assertEqual(outcome.status, "error")
        self.assertIn("redis down", outcome.error)


class EvalUserTests(unittest.TestCase):
    def test_eval_user_is_created_once(self):
        factory = _session_factory()
        with factory() as db:
            first = ensure_eval_user(db)
            second = ensure_eval_user(db)
            count = db.query(User).count()

        self.assertEqual(first, second)
        self.assertEqual(count, 1)


def _outcome(case_id, model_id, status, attempt=1):
    return CaseOutcome(
        case_id=case_id,
        model_id=model_id,
        status=status,
        checks=[CheckOutcome("run_status", status, "")],
        attempt=attempt,
    )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.factory = _session_factory()

    def _suite(self, outcomes, *, status="completed", started_offset=0):
        with self.factory() as db:
            suite_id = store.start_suite_run(
                db, label="t", git_sha=None, suite_sha256="0" * 64, models=["m"], case_ids=["a", "b"]
            )
            run = db.get(EvalSuiteRun, suite_id)
            run.started_at = utc_now() + timedelta(minutes=started_offset)
            db.commit()
            for outcome in outcomes:
                store.save_outcome(db, suite_id, outcome)
            store.finish_suite_run(db, suite_id, status=status, summary=store.summarize(outcomes))
        return suite_id

    def test_summary_counts_per_model(self):
        summary = store.summarize(
            [_outcome("a", "m1", "passed"), _outcome("b", "m1", "failed"), _outcome("a", "m2", "error")]
        )

        self.assertEqual(summary["m1"], {"passed": 1, "failed": 1, "error": 0, "total": 2})
        self.assertEqual(summary["m2"], {"passed": 0, "failed": 0, "error": 1, "total": 1})

    def test_compare_uses_latest_completed_result_per_case_and_model(self):
        older = self._suite([_outcome("a", "m", "failed"), _outcome("b", "m", "failed")], started_offset=-30)
        latest = self._suite([_outcome("a", "m", "passed"), _outcome("b", "m", "failed")], started_offset=-20)
        self._suite([_outcome("a", "m", "failed")], status="failed", started_offset=-10)
        current = [_outcome("a", "m", "failed"), _outcome("b", "m", "passed"), _outcome("c", "m", "failed")]

        with self.factory() as db:
            previous = store.previous_results(
                db,
                before=utc_now(),
                pairs={(o.case_id, o.model_id) for o in current},
                exclude_suite_run_id="current",
            )
        regressions, fixes = store.compare(current, previous)

        self.assertEqual(previous[("a", "m")], ("passed", latest))
        self.assertNotEqual(previous[("b", "m")][1], older)
        self.assertNotIn(("c", "m"), previous)
        self.assertEqual([(c.case_id, c.before, c.after) for c in regressions], [("a", "passed", "failed")])
        self.assertEqual([(c.case_id, c.before, c.after) for c in fixes], [("b", "failed", "passed")])

    def test_recheck_result_is_final_for_summary_and_baseline(self):
        outcomes = [_outcome("a", "m", "failed"), _outcome("a", "m", "passed", attempt=2), _outcome("b", "m", "passed")]
        self._suite(outcomes, started_offset=-10)

        with self.factory() as db:
            previous = store.previous_results(
                db, before=utc_now(), pairs={("a", "m"), ("b", "m")}, exclude_suite_run_id="current"
            )

        self.assertEqual(store.summarize(outcomes)["m"], {"passed": 2, "failed": 0, "error": 0, "total": 2})
        self.assertEqual(previous[("a", "m")][0], "passed")

    def test_errors_are_neither_regressions_nor_fixes(self):
        regressions, fixes = store.compare(
            [_outcome("a", "m", "error"), _outcome("b", "m", "passed")],
            {("a", "m"): ("passed", "s1"), ("b", "m"): ("error", "s1")},
        )

        self.assertEqual((regressions, fixes), ([], []))

    def test_results_keep_trajectory_snapshot(self):
        outcome = _outcome("a", "m", "passed")
        outcome.snapshot = {"mode": "agent", "tool_calls": []}
        suite_id = self._suite([outcome])

        with self.factory() as db:
            row = db.query(EvalCaseResult).filter_by(suite_run_id=suite_id).one()

        self.assertEqual(row.trajectory["mode"], "agent")
        self.assertEqual(row.checks, [{"type": "run_status", "status": "passed", "detail": ""}])


class JudgeTests(unittest.TestCase):
    def test_messages_include_rubric_tool_results_and_answer_but_not_internal_tools(self):
        case = EvalCase.model_validate(
            {
                "id": "sample-case",
                "category": "sample",
                "source": "unit test",
                "message": "后天杭州会下雨吗",
                "setup_turns": ["我在杭州"],
                "checks": [{"type": "judge", "rubric": "回答与工具结果中的天气一致。"}],
            }
        )
        check = case.checks[0]
        snapshot = {
            "tool_calls": [
                {"tool": "update_plan", "arguments": {"items": []}},
                {"tool": "weather_forecast", "arguments": {"location": "杭州"}, "result": {"rain": True}},
            ],
            "answer_text": "后天杭州小雨。",
        }

        user = build_judge_messages(check, case, snapshot)[1]["content"]

        self.assertIn("回答与工具结果中的天气一致", user)
        self.assertIn("我在杭州", user)
        self.assertIn("weather_forecast", user)
        self.assertNotIn("update_plan", user)
        self.assertIn("后天杭州小雨", user)

    def test_malformed_verdict_is_asked_again_once(self):
        from types import SimpleNamespace

        from app.evals import judge as judge_module

        case = EvalCase.model_validate(
            {
                "id": "sample-case",
                "category": "sample",
                "source": "unit test",
                "message": "后天杭州会下雨吗",
                "checks": [{"type": "judge", "rubric": "回答与工具结果中的天气一致。"}],
            }
        )
        replies = iter(['{"type": "json_object", "reason": "漏了 passed"}', '{"passed": true, "reason": "一致"}'])
        calls = []

        async def fake_completion(**kwargs):
            calls.append(kwargs["messages"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))])

        with (
            patch.object(judge_module.llm_manager, "resolve_model", lambda model_id: ("m", "p", {})),
            patch.object(judge_module.litellm, "acompletion", fake_completion),
        ):
            verdict = asyncio.run(judge_module.judge_answer(case.checks[0], case, {"answer_text": "小雨"}))

        self.assertTrue(verdict.passed)
        self.assertEqual(len(calls), 2)

    def test_parse_verdict(self):
        verdict = parse_verdict('{"passed": false, "reason": "编造了温度"}')

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "编造了温度")
        with self.assertRaises(ValueError):
            parse_verdict("not json")


class ScriptTests(unittest.TestCase):
    def test_dry_run_lists_pairs_without_calling_models(self):
        self.assertEqual(run_eval_suite.main(["--models", "m1,m2", "--category", "tool_selection"]), 0)

    def test_plan_respects_model_scope(self):
        cases = [
            EvalCase.model_validate(
                {
                    "id": "all-models",
                    "category": "c",
                    "source": "unit test",
                    "message": "hi",
                    "checks": [{"type": "tool_not_called", "tool": "web_search"}],
                }
            ),
            EvalCase.model_validate(
                {
                    "id": "only-m1",
                    "category": "c",
                    "source": "unit test",
                    "message": "hi",
                    "models": ["m1"],
                    "checks": [{"type": "tool_not_called", "tool": "web_search"}],
                }
            ),
        ]

        pairs = run_eval_suite.plan(cases, ["m1", "m2"])

        self.assertEqual(
            [(case.id, model) for case, model in pairs], [("all-models", "m1"), ("all-models", "m2"), ("only-m1", "m1")]
        )

    def test_argument_errors(self):
        self.assertEqual(run_eval_suite.main([]), 2)
        self.assertEqual(run_eval_suite.main(["--models", "m1", "--case", "no-such-case"]), 2)
        self.assertEqual(run_eval_suite.main(["--list"]), 0)

    def test_regressions_are_rechecked_once_and_only_confirmed_ones_fail(self):
        factory = _session_factory()
        with factory() as db:
            baseline = store.start_suite_run(
                db, label="t", git_sha=None, suite_sha256="0" * 64, models=["m"], case_ids=[]
            )
            run = db.get(EvalSuiteRun, baseline)
            run.started_at = utc_now() - timedelta(minutes=10)
            db.commit()
            for case_id in ("unambiguous-route", "greeting-no-escalation", "poem-no-escalation"):
                store.save_outcome(db, baseline, _outcome(case_id, "m", "passed"))
            store.finish_suite_run(db, baseline, status="completed", summary={})

        scripted = {
            ("unambiguous-route", 1): "failed",
            ("unambiguous-route", 2): "failed",
            ("greeting-no-escalation", 1): "failed",
            ("greeting-no-escalation", 2): "passed",
            ("poem-no-escalation", 1): "passed",
        }
        calls = []

        async def fake_run_case(case, model, *, user_id, session_factory, judge, attempt=1):
            calls.append((case.id, attempt))
            return _outcome(case.id, model, scripted[(case.id, attempt)], attempt=attempt)

        @asynccontextmanager
        async def fake_runtime():
            yield

        args = run_eval_suite.build_parser().parse_args(["--models", "m", "--fail-on-regression", "--apply"])
        cases = [
            case
            for case in run_eval_suite.load_suite().cases
            if case.id in {"unambiguous-route", "greeting-no-escalation", "poem-no-escalation"}
        ]
        with (
            patch("app.db.database.SessionLocal", factory),
            patch.object(runner, "run_case", fake_run_case),
            patch.object(runner, "eval_runtime", fake_runtime),
        ):
            code = asyncio.run(run_eval_suite._execute(args, cases, run_eval_suite.plan(cases, ["m"]), "0" * 64))

        self.assertEqual(code, 1)
        self.assertEqual(
            sorted(c for c in calls if c[1] == 2), [("greeting-no-escalation", 2), ("unambiguous-route", 2)]
        )
        with factory() as db:
            rows = db.query(EvalCaseResult).filter(EvalCaseResult.suite_run_id != baseline).count()
            current = db.query(EvalSuiteRun).filter(EvalSuiteRun.id != baseline).one()
        self.assertEqual(rows, 5)
        self.assertEqual(current.summary["m"], {"passed": 2, "failed": 1, "error": 0, "total": 3})


if __name__ == "__main__":
    unittest.main()
