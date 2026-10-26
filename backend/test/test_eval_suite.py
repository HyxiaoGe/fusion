import asyncio
import json
import unittest
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
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
from app.evals import usage as usage_module
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
from app.evals.usage import TokenUsage, is_off_peak, load_runs_usage, round_cost
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


_PRICED = {
    "token_costs": {
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 2e-6,
        "cache_read_input_token_cost": 1e-7,
        "input_cost_per_token_above_200k_tokens": 3e-6,
        "output_cost_per_token_above_200k_tokens": 4e-6,
    }
}
# DeepSeek 式时段价：北京时间工作日 9–12、14–18 高峰，其余时间与周末半价。
_OFF_PEAK = {
    "input_cost_per_token": 5e-7,
    "output_cost_per_token": 1e-6,
    "cache_read_input_token_cost": 5e-8,
    "windows": [
        {"weekdays": [1, 2, 3, 4, 5], "hours_utc": ["10:00-01:00", "04:00-06:00"]},
        {"weekdays": [6, 7], "hours_utc": "00:00-00:00"},
    ],
    "weekday_timezone": "Asia/Shanghai",
}


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

    def test_judge_view_matches_model_context_with_citations(self):
        """裁判看到的来源与模型一致：带回答用的引用编号、按同一上限截取，轨迹缺的来源仍列出。"""
        raw_sources = [
            {"url": "https://a.example/1", "title": "A", "content": "长原文" * 20000},
            {"url": "https://b.example/2", "title": "B", "description": "B 的摘要"},
        ]
        refs = [
            {"url": "https://a.example/1", "title": "A", "citation_index": 11},
            {"url": "https://b.example/2", "title": "B", "citation_index": 12},
            {"url": "https://c.example/3", "title": "C", "citation_index": 13},
        ]
        snapshot = self._snapshot(
            tool_logs=[
                {
                    "id": "log-search",
                    "tool_name": "web_search",
                    "status": "success",
                    "detail": {
                        "payload": {"query": "q", "context_source_limit": 10},
                        "result": {"sources": raw_sources},
                    },
                },
                {
                    "id": "log-read",
                    "tool_name": "url_read",
                    "status": "success",
                    "detail": {
                        "payload": {"url": "https://a.example/1"},
                        "result": {"url": "https://a.example/1", "title": "A", "content": "正" * 9000},
                    },
                },
            ],
            answer_content=[
                {"type": "search", "tool_call_log_id": "log-search", "source_refs": refs},
                {"type": "url_read", "tool_call_log_id": "log-read", "source_refs": refs[:1]},
                {"type": "text", "text": "见 [11][13]。"},
            ],
            result_limit=JUDGE_TOOL_RESULT_CHAR_LIMIT,
            model_view=True,
        )

        search, page = (call["result"] for call in snapshot["tool_calls"])
        self.assertEqual([source["citation"] for source in search["sources"]], [11, 12, 13])
        self.assertEqual(len(search["sources"][0]["content"]), 1000)
        self.assertEqual(search["sources"][1]["content"], "B 的摘要")
        self.assertTrue(search["sources"][2]["content_unavailable"])
        self.assertEqual(page["citation"], 11)
        self.assertEqual(len(page["content"]), 8000)
        self.assertTrue(page["content_truncated"])

    def test_judge_view_marks_sources_beyond_context_limit_as_title_only(self):
        snapshot = self._snapshot(
            tool_logs=[
                {
                    "id": "log-search",
                    "tool_name": "web_search",
                    "status": "success",
                    "detail": {
                        "payload": {"query": "q", "context_source_limit": 1},
                        "result": {"sources": [{"url": "u1", "content": "c1"}, {"url": "u2", "content": "c2"}]},
                    },
                }
            ],
            answer_content=None,
            result_limit=JUDGE_TOOL_RESULT_CHAR_LIMIT,
            model_view=True,
        )

        sources = snapshot["tool_calls"][0]["result"]["sources"]
        self.assertEqual([source["content"] for source in sources], ["c1", None])
        self.assertEqual([source["citation"] for source in sources], [None, None])

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

        self.assertEqual(
            summary["m1"], {"passed": 1, "failed": 1, "error": 0, "total": 2, "cost_usd": 0.0, "judge_cost_usd": 0.0}
        )
        self.assertEqual(
            summary["m2"], {"passed": 0, "failed": 0, "error": 1, "total": 1, "cost_usd": 0.0, "judge_cost_usd": 0.0}
        )

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

        self.assertEqual(
            store.summarize(outcomes)["m"],
            {"passed": 2, "failed": 0, "error": 0, "total": 2, "cost_usd": 0.0, "judge_cost_usd": 0.0},
        )
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
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))],
                usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100, prompt_tokens_details=None),
            )

        with (
            patch.object(judge_module.llm_manager, "resolve_model", lambda model_id: ("m", "p", {})),
            patch.object(judge_module.litellm, "acompletion", fake_completion),
            patch.object(usage_module.litellm_catalog, "get_model_entry", lambda alias: _PRICED),
        ):
            verdict = asyncio.run(judge_module.judge_answer(case.checks[0], case, {"answer_text": "小雨"}))

        self.assertTrue(verdict.passed)
        self.assertEqual(len(calls), 2)
        # 格式错误那次也花了钱，两次的用量都要算进裁判成本。
        self.assertEqual((verdict.usage.input_tokens, verdict.usage.output_tokens), (2000, 200))
        self.assertAlmostEqual(verdict.usage.cost_usd, 2000 * 1e-6 + 200 * 2e-6)

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
        self.assertEqual(
            current.summary["m"],
            {"passed": 2, "failed": 1, "error": 0, "total": 3, "cost_usd": 0.0, "judge_cost_usd": 0.0},
        )


if __name__ == "__main__":
    unittest.main()


class UsageTests(unittest.TestCase):
    def test_round_cost_uses_cache_price_and_tier(self):
        costs = _PRICED["token_costs"]

        self.assertAlmostEqual(
            round_cost(costs, input_tokens=10_000, output_tokens=500, cache_read_tokens=8_000),
            2_000 * 1e-6 + 8_000 * 1e-7 + 500 * 2e-6,
        )
        # 没登记缓存价时命中缓存的输入按普通输入价计，算出的是上限。
        no_cache_price = {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}
        self.assertAlmostEqual(
            round_cost(no_cache_price, input_tokens=10_000, output_tokens=0, cache_read_tokens=8_000), 10_000 * 1e-6
        )
        self.assertAlmostEqual(
            round_cost(costs, input_tokens=250_000, output_tokens=1_000, cache_read_tokens=0),
            250_000 * 3e-6 + 1_000 * 4e-6,
        )
        self.assertIsNone(round_cost({}, input_tokens=1, output_tokens=1, cache_read_tokens=0))

    def test_round_cost_switches_to_off_peak_rates_by_request_time(self):
        costs = {**_PRICED["token_costs"], "off_peak_pricing": _OFF_PEAK}
        peak_monday_10 = datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)  # 北京周一 10:00
        lunch_monday_13 = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)  # 北京周一 13:00
        saturday_10 = datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc)  # 北京周六 10:00
        monday_03 = datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc)  # 北京周一 03:00，UTC 还是周日
        standard = 2_000 * 1e-6 + 8_000 * 1e-7 + 500 * 2e-6
        discounted = 2_000 * 5e-7 + 8_000 * 5e-8 + 500 * 1e-6
        kwargs = dict(input_tokens=10_000, output_tokens=500, cache_read_tokens=8_000)

        self.assertAlmostEqual(round_cost(costs, **kwargs, at=peak_monday_10), standard)
        self.assertAlmostEqual(round_cost(costs, **kwargs, at=lunch_monday_13), discounted)
        self.assertAlmostEqual(round_cost(costs, **kwargs, at=saturday_10), discounted, msg="周末整天空闲")
        self.assertAlmostEqual(round_cost(costs, **kwargs, at=monday_03), discounted, msg="星期按北京日历看")
        self.assertAlmostEqual(round_cost(costs, **kwargs), standard, msg="不知道时刻按标准价")
        # naive 时间按 UTC 解释；空闲块缺的档位沿用标准价。
        partial = {**costs, "off_peak_pricing": {**_OFF_PEAK, "cache_read_input_token_cost": None}}
        self.assertAlmostEqual(
            round_cost(partial, **kwargs, at=lunch_monday_13.replace(tzinfo=None)),
            2_000 * 5e-7 + 8_000 * 1e-7 + 500 * 1e-6,
        )
        # 200k 分档也被空闲价整体替换，与 LiteLLM 一致。
        self.assertAlmostEqual(
            round_cost(costs, input_tokens=250_000, output_tokens=1_000, cache_read_tokens=0, at=lunch_monday_13),
            250_000 * 5e-7 + 1_000 * 1e-6,
        )

    def test_is_off_peak_handles_flat_hours_wrap_and_malformed_rules(self):
        flat = {"hours_utc": "16:30-00:30"}
        self.assertTrue(is_off_peak(flat, datetime(2026, 10, 26, 23, 59, tzinfo=timezone.utc)))
        self.assertTrue(is_off_peak(flat, datetime(2026, 10, 26, 0, 29, tzinfo=timezone.utc)))
        self.assertFalse(is_off_peak(flat, datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)), "止不含")
        self.assertTrue(is_off_peak({"hours_utc": ["00:00-00:00"]}, datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)))
        named = {"windows": [{"weekdays": ["sat", "Sunday"], "hours_utc": "00:00-00:00"}]}
        self.assertTrue(is_off_peak(named, datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)))
        self.assertFalse(is_off_peak(named, datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)))
        for broken in (
            {},
            {"hours_utc": "nope"},
            {"windows": "x"},
            {"windows": [{"weekdays": "1-5", "hours_utc": "00:00-00:00"}]},
        ):
            self.assertFalse(is_off_peak(broken, datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)), broken)

    def test_runs_usage_prices_each_round_by_its_own_model(self):
        factory = _session_factory()
        with factory() as db:
            user = User(username="u1")
            db.add(user)
            db.flush()
            conv = Conversation(user_id=user.id, title="t", model_id="primary")
            db.add(conv)
            db.flush()
            for run_id in ("run-1", "run-2"):
                db.add(
                    AgentSession(
                        id=run_id,
                        conversation_id=conv.id,
                        user_id=user.id,
                        model_id="primary",
                        provider="litellm",
                        status="completed",
                    )
                )
            db.flush()
            peak = datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)  # 北京周一 10:00
            off_peak = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)  # 北京周一 13:00
            events = [
                # r1 在空闲时段开始、高峰时段完成：按开始时刻计空闲价。
                ("run-1", "llm_round_started", {"llm_round_id": "r1", "model": "primary"}, off_peak),
                (
                    "run-1",
                    "llm_round_completed",
                    {"llm_round_id": "r1", "input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 800},
                    peak,
                ),
                # 原模型不可用后换了备用模型，这一轮要按备用模型计价。
                ("run-1", "llm_round_started", {"llm_round_id": "r2", "model": "fallback"}, peak),
                (
                    "run-1",
                    "llm_round_completed",
                    {"llm_round_id": "r2", "input_tokens": 2000, "output_tokens": 50},
                    peak,
                ),
                # 没有开始事件的轮次退回完成时刻（高峰）。
                (
                    "run-2",
                    "llm_round_completed",
                    {"llm_round_id": "r3", "input_tokens": 500, "output_tokens": 10},
                    peak,
                ),
                (
                    "run-other",
                    "llm_round_completed",
                    {"llm_round_id": "r9", "input_tokens": 9999, "output_tokens": 9},
                    peak,
                ),
            ]
            for sequence, (run_id, event_type, payload, event_ts) in enumerate(events, start=1):
                db.add(
                    AgentEvent(
                        event_id=uuid.uuid4(),
                        conversation_id=conv.id,
                        run_id=run_id,
                        sequence=sequence,
                        event_type=event_type,
                        event_ts=event_ts,
                        payload=payload,
                    )
                )
            db.commit()

            priced = {"token_costs": {**_PRICED["token_costs"], "off_peak_pricing": _OFF_PEAK}}
            prices = {"primary": priced, "fallback": {}}
            with patch.object(usage_module.litellm_catalog, "get_model_entry", prices.get):
                usage = load_runs_usage(db, ["run-1", "run-2", None])

        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_read_tokens), (3500, 160, 800))
        self.assertAlmostEqual(usage.cost_usd, (200 * 5e-7 + 800 * 5e-8 + 100 * 1e-6) + (500 * 1e-6 + 10 * 2e-6))
        self.assertEqual(usage.unpriced_models, ("fallback",))


class CaseUsageTests(unittest.TestCase):
    def test_run_case_counts_setup_turns_and_judge_usage(self):
        seen_run_ids = []
        judge_usage = TokenUsage(input_tokens=300, output_tokens=20, cost_usd=0.001)

        async def send(db, turn, **kwargs):
            turn.run_id, turn.conversation_id = f"run-{len(seen_run_ids) + 1}", "conv-1"
            seen_run_ids.append(turn.run_id)

        async def judge(check, case, snapshot):
            from app.evals.checks import JudgeVerdict

            return JudgeVerdict(True, "一致", judge_usage)

        def fake_usage(db, run_ids):
            self.assertEqual(run_ids, ["run-1", "run-2"])
            return TokenUsage(input_tokens=1000, output_tokens=100, cost_usd=0.01)

        case = _case_with(
            setup_turns=["我在杭州"], checks=[{"type": "judge", "rubric": "回答与工具结果中的天气一致。"}]
        )
        snapshot = {"run_id": "run-2", "status": "completed", "tool_calls": [], "answer_text": "小雨"}

        async def wait_terminal(factory, run_id):
            return "completed"

        with (
            patch.object(runner, "_send_turn", send),
            patch.object(runner, "_wait_terminal", wait_terminal),
            patch.object(runner, "load_snapshot", lambda db, run_id, **kwargs: snapshot),
            patch.object(runner, "load_runs_usage", fake_usage),
        ):
            outcome = asyncio.run(runner.run_case(case, "m", user_id="u", session_factory=nullcontext, judge=judge))

        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.usage.cost_usd, 0.01)
        self.assertEqual(outcome.judge_usage, judge_usage)

    def test_usage_is_saved_and_rechecks_count_toward_cost(self):
        factory = _session_factory()
        first = _outcome("a", "m", "failed")
        first.usage = TokenUsage(input_tokens=1000, output_tokens=10, cost_usd=0.02)
        first.judge_usage = TokenUsage(input_tokens=100, output_tokens=5, cost_usd=0.001)
        recheck = _outcome("a", "m", "passed", attempt=2)
        recheck.usage = TokenUsage(input_tokens=900, output_tokens=10, cost_usd=0.03)
        unread = _outcome("b", "m", "error")

        with factory() as db:
            suite_id = store.start_suite_run(
                db, label="t", git_sha=None, suite_sha256="0" * 64, models=["m"], case_ids=["a", "b"]
            )
            for outcome in (first, recheck, unread):
                store.save_outcome(db, suite_id, outcome)
            rows = {
                (row.case_id, row.attempt): row.usage
                for row in db.query(EvalCaseResult).filter_by(suite_run_id=suite_id)
            }

        self.assertEqual(rows[("a", 1)]["model"]["cost_usd"], 0.02)
        self.assertEqual(rows[("a", 1)]["judge"]["input_tokens"], 100)
        self.assertIsNone(rows[("b", 1)]["model"])
        summary = store.summarize([first, recheck, unread])["m"]
        self.assertEqual((summary["passed"], summary["error"]), (1, 1))
        self.assertAlmostEqual(summary["cost_usd"], 0.05)
        self.assertAlmostEqual(summary["judge_cost_usd"], 0.001)

        report = run_eval_suite.format_report([first, recheck, unread], [], [], [])
        self.assertIn("本轮估算花费 $0.0510", report)
        self.assertIn("a @ m（复核）", report)
        self.assertIn("b @ m  0s  用量未读到", report)
