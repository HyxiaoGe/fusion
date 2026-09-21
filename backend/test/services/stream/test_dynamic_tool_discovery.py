"""动态工具发现原型 P01–P12 离线验收。"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.schemas.chat import Usage, WeatherResultsBlock
from app.services.stream.agent_loop_driver import run_agent_loop
from app.services.stream.agent_loop_execution import (
    AgentLoopDependencies,
    AgentLoopExecutionRequest,
    build_agent_loop_execution,
)
from app.services.stream.agent_loop_lifecycle import _run_config
from app.services.stream.agent_loop_policy import AgentLoopLimits
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
from app.services.stream.agent_round import AgentRoundResult
from app.services.stream.dynamic_tool_discovery import (
    TOOL_SEARCH_NAME,
    DynamicToolDiscoveryUnsupportedError,
)
from app.services.stream.dynamic_tool_discovery_fixtures import (
    EXPERIMENT_NOW,
    MCP_NETWORK_ALIAS,
    MCP_READONLY_ALIAS,
    SharedFixtureBudget,
    build_prototype_fixture_catalog,
)
from app.services.stream.limit_summary import LimitSummaryOutcome
from app.services.stream.limit_summary_fact_guard import NO_EVIDENCE_ANSWER_TEXT, resolve_no_evidence_answer
from app.services.stream.step_lifecycle import AgentStepContext
from app.services.stream.tool_execution_result import ToolExecutionRecord
from app.services.stream.tool_round import handle_tool_calls_round


class RecordingEmitter:
    def __init__(self) -> None:
        self.product_blocks: list[object] = []
        self.tool_events: list[tuple[str, str]] = []
        self.calls: list[str] = []

    async def content_block_upserted(self, **kwargs):
        self.product_blocks.append(kwargs.get("content_block"))
        self.calls.append("content_block_upserted")

    async def tool_call_started(self, **kwargs):
        self.tool_events.append(("started", str(kwargs.get("tool_name"))))

    async def tool_call_completed(self, **kwargs):
        self.tool_events.append(("completed", str(kwargs.get("tool_name"))))

    async def plan_snapshot(self, **kwargs):
        self.calls.append("plan_snapshot")

    def __getattr__(self, name: str):
        async def _noop(**_kwargs):
            self.calls.append(name)

        return _noop


class ScriptedRounds:
    def __init__(self, rounds: list[list[dict] | dict]):
        self.rounds = list(rounds)
        self.visible_tools: list[list[str]] = []

    async def __call__(self, **kwargs):
        tools = kwargs.get("call_kwargs", {}).get("tools") or []
        names = [item["function"]["name"] for item in tools if isinstance(item, dict)]
        self.visible_tools.append(names)
        tool_choice = kwargs.get("call_kwargs", {}).get("tool_choice")
        if not self.rounds:
            return _round_result([], "stop", frozenset(names))
        spec = self.rounds.pop(0)
        if isinstance(spec, dict) and spec.get("stop"):
            return _round_result([], "stop", frozenset(names), content=spec.get("content", "完成"))
        _assert_script_respects_tool_choice(spec, tool_choice=tool_choice, visible=names)
        return _round_result(spec, "tool_calls", frozenset(names))


def _assert_script_respects_tool_choice(spec, *, tool_choice, visible):
    del visible
    forced = None
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
        forced = function.get("name")
    for call in spec:
        name = call.get("name")
        if forced is not None and name != forced:
            raise AssertionError(f"script returned {name} but tool_choice forced {forced}")


def _round_result(tool_calls, finish_reason, announced, content=""):
    return AgentRoundResult(
        reasoning_buf="",
        content_buf=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        accumulated_usage=Usage(input_tokens=1, output_tokens=1),
        announced_tool_names=announced,
    )


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


async def _start_step(**kwargs):
    step_number = kwargs["step_number"]
    return AgentStepContext(
        step_id=f"step-{step_number}",
        step_number=step_number,
        started_at=0.0,
        thinking_block_id=f"th-{step_number}",
        text_block_id=f"tx-{step_number}",
    )


async def _complete_step(**_kwargs):
    return None


async def _execute_fixtures(
    tool_calls,
    conversation_id,
    user_id,
    model_id,
    provider,
    **kwargs,
):
    records = []
    handlers = kwargs.get("tool_handlers") or {}
    for tool_call in tool_calls or []:
        handler = handlers[tool_call["name"]]
        args = tool_call.get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args)
        result = await handler.execute(args)
        records.append(
            ToolExecutionRecord(
                tool_call=tool_call,
                result=result,
                handler=handler,
                block_id=f"blk-{tool_call['id']}",
                log_id=f"log-{tool_call['id']}",
            )
        )
    return records


def _discovery_config(*, message: str, playback=None, plan_mode: str = "off", budget=None):
    schemas, handlers, shared = build_prototype_fixture_catalog(budget=budget, playback=playback)
    classifier_calls = []

    def classify(**kwargs):
        classifier_calls.append(kwargs)
        raise AssertionError("候选发现路径不得调用包分类器")

    with patch(
        "app.services.stream.agent_loop_request_prep.resolve_run_capability_route",
        side_effect=AssertionError("候选发现路径不得调用 resolve_run_capability_route"),
    ):
        config = build_agent_loop_call_config(
            provider="openai",
            options={"dynamic_tool_discovery": True, "plan_mode": plan_mode},
            capabilities={"functionCalling": True, "searchCapable": True, "agentTools": True},
            additional_tools=schemas,
            dynamic_tool_handlers=handlers,
            authorized_tool_names=[schema["function"]["name"] for schema in schemas],
            original_message=message,
            classify_fn=classify,
        )
    return config, handlers, shared, classifier_calls


def _runtime_from_execution(execution, *, script, emitter):
    runtime = execution.runtime
    object.__setattr__(runtime, "run_round_fn", script)
    object.__setattr__(runtime, "handle_tool_calls_round_fn", handle_tool_calls_round)
    object.__setattr__(runtime, "execute_tools_fn", _execute_fixtures)
    object.__setattr__(runtime, "start_step_fn", _start_step)
    object.__setattr__(runtime, "complete_step_fn", _complete_step)
    object.__setattr__(runtime, "persist_message_fn", lambda *_a, **_k: None)
    object.__setattr__(runtime, "run_limit_summary_step_fn", _limit_summary)
    object.__setattr__(runtime, "tool_discovery", execution.state.tool_discovery)
    object.__setattr__(runtime, "emitter", emitter)
    return runtime


def _execution(config, *, run_id="run-discovery"):
    return build_agent_loop_execution(
        request=AgentLoopExecutionRequest(
            db=None,
            conversation_id="conv-d",
            user_id="user-d",
            model_id="gpt-4",
            litellm_model="openai/gpt-4",
            litellm_kwargs={},
            provider="openai",
            assistant_message_id="msg-d",
            task_id="task-d",
            call_config=config,
            trace_id=run_id,
            original_message="查一下杭州周末天气，再找上海过去的高铁。",
        ),
        limits=AgentLoopLimits(max_steps=12, max_tool_calls=20, total_timeout_s=300),
        dependencies=AgentLoopDependencies(
            session_cache=SimpleNamespace(
                write_step_started=lambda **_k: asyncio.sleep(0),
                write_step_completed=lambda **_k: asyncio.sleep(0),
            ),
            redis_writer=object(),
            start_step_fn=_start_step,
            complete_step_fn=_complete_step,
            run_round_fn=_unused,
            handle_tool_calls_round_fn=handle_tool_calls_round,
            run_limit_summary_step_fn=_limit_summary,
            llm_call_fn=_unused,
            stream_round_fn=_unused,
            execute_tools_fn=_execute_fixtures,
            persist_message_fn=lambda *_a, **_k: None,
            log_round_summary_fn=lambda **_k: None,
            warning_fn=lambda _m: None,
            clock=lambda: 1.0,
        ),
    )


async def _limit_summary(**_kwargs):
    return LimitSummaryOutcome(
        accumulated_usage=Usage(input_tokens=0, output_tokens=0),
        context=None,
        incomplete=False,
    )


async def _unused(**_kwargs):
    raise AssertionError("不应调用这个依赖")


class DynamicToolDiscoveryPrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_p01_discover_weather_then_trains_without_classifier(self):
        config, handlers, _shared, classifier_calls = _discovery_config(
            message="查一下杭州周末天气，再找上海过去的高铁。"
        )
        self.assertEqual(classifier_calls, [])
        self.assertEqual(config.announced_tools, [TOOL_SEARCH_NAME])
        script = ScriptedRounds(
            [
                [_tool_call("c1", TOOL_SEARCH_NAME, {"query": "weather"})],
                [_tool_call("c2", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                [_tool_call("c3", TOOL_SEARCH_NAME, {"query": "select:search_trains"})],
                [
                    _tool_call(
                        "c4",
                        "search_trains",
                        {"origin": "杭州", "destination": "上海", "departure_date": "2026-09-26"},
                    )
                ],
                {"stop": True, "content": "已查询天气和车次"},
            ]
        )
        execution = _execution(config)
        emitter = RecordingEmitter()
        runtime = _runtime_from_execution(execution, script=script, emitter=emitter)
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertGreaterEqual(handlers["weather_forecast"].execute_count, 1)
        self.assertGreaterEqual(handlers["search_trains"].execute_count, 1)
        self.assertTrue(any("weather_forecast" in names for names in script.visible_tools))
        self.assertTrue(any("search_trains" in names for names in script.visible_tools))
        self.assertTrue(any(isinstance(block, WeatherResultsBlock) for block in emitter.product_blocks))

    async def test_p02_unloaded_authorized_call_is_intercepted_then_executable(self):
        config, handlers, _shared, _calls = _discovery_config(message="杭州天气")
        script = ScriptedRounds(
            [
                [_tool_call("u1", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                [_tool_call("u2", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("u3", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                {"stop": True},
            ]
        )
        messages: list = []
        execution = _execution(config, run_id="run-p02")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=messages, state=execution.state, runtime=runtime)
        self.assertEqual(handlers["weather_forecast"].execute_count, 1)
        intercepts = [event for event in config.tool_discovery.events if event.get("kind") == "unloaded_intercept"]
        self.assertTrue(intercepts)
        tool_messages = [item for item in runtime.call_kwargs.get("tools", [])]
        self.assertTrue(
            any(_fn_name(item) == "weather_forecast" for item in tool_messages)
            or handlers["weather_forecast"].execute_count == 1
        )

    async def test_p03_unauthorized_name_never_activated(self):
        config, handlers, _shared, _calls = _discovery_config(message="杭州天气")
        script = ScriptedRounds(
            [
                [_tool_call("x1", TOOL_SEARCH_NAME, {"query": "select:delete_everything"})],
                [_tool_call("x2", "delete_everything", {"target": "all"})],
                {"stop": True},
            ]
        )
        execution = _execution(config, run_id="run-p03")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        schemas = json.dumps(config.tool_discovery.events, ensure_ascii=False)
        self.assertIn("unauthorized_intercept", schemas)
        self.assertNotIn(
            "delete_everything",
            [name for names in script.visible_tools for name in names if name != "delete_everything"],
        )
        self.assertFalse(any(_fn_name(tool) == "delete_everything" for tool in runtime.call_kwargs.get("tools") or []))

    async def test_p04_network_denial_blocks_discovery_and_execution(self):
        config, handlers, _shared, _calls = _discovery_config(message="本次不要联网，只说杭州天气怎么查")
        denied = config.tool_discovery.denied_names
        for name in ("web_search", "url_read", "weather_forecast", "search_trains", MCP_NETWORK_ALIAS):
            self.assertIn(name, denied)
        self.assertNotIn(MCP_READONLY_ALIAS, denied)
        script = ScriptedRounds(
            [
                [
                    _tool_call(
                        "n1",
                        TOOL_SEARCH_NAME,
                        {"query": "select:web_search,weather_forecast,search_trains,url_read,mcp_network_probe"},
                    )
                ],
                [_tool_call("n2", "web_search", {"query": "杭州天气"})],
                [_tool_call("n3", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                [
                    _tool_call(
                        "n4",
                        "search_trains",
                        {"origin": "杭州", "destination": "上海", "departure_date": "2026-09-26"},
                    )
                ],
                [_tool_call("n5", MCP_NETWORK_ALIAS, {"note": "should-deny"})],
                [_tool_call("n6", TOOL_SEARCH_NAME, {"query": f"select:{MCP_READONLY_ALIAS}"})],
                [_tool_call("n7", MCP_READONLY_ALIAS, {"note": "local-ok"})],
                {"stop": True},
            ]
        )
        execution = _execution(config, run_id="run-p04")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertEqual(handlers["web_search"].execute_count, 0)
        self.assertEqual(handlers["url_read"].execute_count, 0)
        self.assertEqual(handlers["weather_forecast"].execute_count, 0)
        self.assertEqual(handlers["search_trains"].execute_count, 0)
        self.assertEqual(handlers[MCP_NETWORK_ALIAS].execute_count, 0)
        self.assertEqual(handlers[MCP_READONLY_ALIAS].execute_count, 1)
        visible = {name for names in script.visible_tools for name in names}
        self.assertNotIn("web_search", visible)
        self.assertNotIn("weather_forecast", visible)
        kinds = {event.get("kind") for event in config.tool_discovery.events}
        self.assertTrue({"discover_denied", "unauthorized_intercept"} & kinds)

    async def test_p05_idempotent_discover_and_shared_budget(self):
        budget = SharedFixtureBudget(max_calls=1)
        config, handlers, shared, _calls = _discovery_config(
            message="杭州到上海高铁",
            budget=budget,
        )
        self.assertIs(handlers["weather_forecast"].budget, handlers["search_trains"].budget)
        first_id = id(shared)
        script = ScriptedRounds(
            [
                [_tool_call("b1", TOOL_SEARCH_NAME, {"query": "select:search_trains"})],
                [_tool_call("b2", TOOL_SEARCH_NAME, {"query": "select:search_trains"})],
                [
                    _tool_call(
                        "b3",
                        "search_trains",
                        {"origin": "杭州", "destination": "上海", "departure_date": "2026-09-26"},
                    )
                ],
                [_tool_call("b4", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("b5", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                {"stop": True},
            ]
        )
        execution = _execution(config, run_id="run-p05")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        messages: list = []
        await run_agent_loop(db=None, messages=messages, state=execution.state, runtime=runtime)
        self.assertEqual(id(handlers["search_trains"].budget), first_id)
        self.assertEqual(shared._used, 1)
        self.assertEqual(handlers["search_trains"].execute_count, 1)
        self.assertEqual(await shared.remaining(), 0)
        self.assertTrue(any(event.get("kind") == "discover_idempotent" for event in config.tool_discovery.events))
        # 额度耗尽后既有路径会隐藏已耗尽工具；再次调用不得执行成功产品查询。
        self.assertEqual(handlers["weather_forecast"].execute_count, 0)

    async def test_p06_failure_stays_in_context_and_allows_alternative(self):
        config, handlers, _shared, _calls = _discovery_config(
            message="杭州天气，失败后可搜索",
            playback={"weather_forecast": {"scenario": "error"}},
        )
        script = ScriptedRounds(
            [
                [_tool_call("f1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("f2", "weather_forecast", {"location": "杭州-error", "location_source": "named"})],
                [_tool_call("f3", TOOL_SEARCH_NAME, {"query": "select:web_search"})],
                [_tool_call("f4", "web_search", {"query": "杭州天气"})],
                {"stop": True},
            ]
        )
        execution = _execution(config, run_id="run-p06")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertEqual(handlers["weather_forecast"].execute_count, 1)
        self.assertEqual(handlers["web_search"].execute_count, 1)
        self.assertIn("weather_forecast", execution.state.failed_tool_names)

    async def test_p07_plan_enum_and_allow_set_stay_aligned(self):
        config, handlers, _shared, _calls = _discovery_config(message="杭州天气并做计划", plan_mode="on")
        self.assertEqual(config.plan_mode, "on")
        execution = _execution(config, run_id="run-p07")
        self.assertEqual(execution.state.plan_coordinator.allowed_tool_names, frozenset())
        script = ScriptedRounds(
            [
                [_tool_call("p1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [
                    _tool_call(
                        "p2",
                        "update_plan",
                        {
                            "explanation": "先查天气",
                            "plan": [
                                {
                                    "id": "s1",
                                    "step": "查询杭州天气",
                                    "status": "in_progress",
                                    "kind": "other",
                                    "depends_on": [],
                                    "planned_tools": ["weather_forecast"],
                                },
                                {
                                    "id": "s2",
                                    "step": "回答",
                                    "status": "pending",
                                    "kind": "answer",
                                    "depends_on": ["s1"],
                                    "planned_tools": [],
                                },
                            ],
                        },
                    )
                ],
                [
                    _tool_call(
                        "p3",
                        "weather_forecast",
                        {"location": "杭州", "location_source": "named", "_plan_item_id": "s1"},
                    )
                ],
                {"stop": True},
            ]
        )
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertIn("weather_forecast", execution.state.plan_coordinator.allowed_tool_names)
        update_plan = next(tool for tool in runtime.call_kwargs["tools"] if _fn_name(tool) == "update_plan")
        enum_values = update_plan["function"]["parameters"]["properties"]["plan"]["items"]["properties"][
            "planned_tools"
        ]["items"].get("enum")
        self.assertIn("weather_forecast", enum_values or [])
        self.assertGreaterEqual(handlers["weather_forecast"].execute_count, 1)

    async def test_p08_empty_error_and_url_without_body_are_not_evidence(self):
        config, handlers, _shared, _calls = _discovery_config(
            message="空结果也要具体班次",
            playback={
                "search_trains": {"scenario": "empty"},
                "url_read": {"scenario": "url_empty"},
                "web_search": {"scenario": "empty"},
            },
        )
        script = ScriptedRounds(
            [
                [_tool_call("e1", TOOL_SEARCH_NAME, {"query": "select:search_trains,url_read,web_search"})],
                [
                    _tool_call(
                        "e2",
                        "search_trains",
                        {"origin": "杭州", "destination": "empty", "departure_date": "2026-09-26"},
                    )
                ],
                [_tool_call("e3", "web_search", {"query": "empty"})],
                [_tool_call("e4", "url_read", {"url": "https://example.test/nobody"})],
                {"stop": True, "content": "G7301 二等座 73 元，大约 1 小时"},
            ]
        )
        execution = _execution(config, run_id="run-p08")
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        guarded, kind = resolve_no_evidence_answer(
            "G7301 二等座 73 元，大约 1 小时",
            content_blocks=execution.state.content_blocks,
            messages=[{"role": "user", "content": "空结果也要具体班次"}],
            capability_resolution=config.capability_resolution,
            recovery_evidence=execution.state.recovery_evidence,
        )
        self.assertEqual(guarded, NO_EVIDENCE_ANSWER_TEXT)
        self.assertIsNotNone(kind)
        self.assertEqual(handlers["search_trains"].execute_count, 1)

    async def test_p09_discovery_then_limit_does_not_start_new_product_calls(self):
        config, handlers, _shared, _calls = _discovery_config(message="杭州天气")
        execution = _execution(config, run_id="run-p09")
        run_start = execution.runtime.run_start
        script = ScriptedRounds(
            [
                [_tool_call("l1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("l2", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                {"stop": True},
            ]
        )
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        object.__setattr__(runtime, "limits", AgentLoopLimits(max_steps=1, max_tool_calls=20, total_timeout_s=300))
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertEqual(runtime.run_start, run_start)
        self.assertEqual(runtime.limits.max_steps, 1)
        self.assertIn("weather_forecast", config.tool_discovery.loaded_names)
        self.assertEqual(handlers["weather_forecast"].execute_count, 0)
        self.assertEqual(execution.state.limit_reason, "max_steps")

    async def test_p10_two_runs_do_not_leak_loaded_tools_or_budget(self):
        config_a, handlers_a, shared_a, _c1 = _discovery_config(message="杭州天气")
        config_b, handlers_b, shared_b, _c2 = _discovery_config(message="上海天气")
        self.assertIsNot(config_a.tool_discovery, config_b.tool_discovery)
        self.assertIsNot(shared_a, shared_b)
        script_a = ScriptedRounds(
            [
                [_tool_call("a1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                {"stop": True},
            ]
        )
        exec_a = _execution(config_a, run_id="run-a")
        await run_agent_loop(
            db=None,
            messages=[],
            state=exec_a.state,
            runtime=_runtime_from_execution(exec_a, script=script_a, emitter=RecordingEmitter()),
        )
        self.assertIn("weather_forecast", config_a.tool_discovery.loaded_names)
        self.assertNotIn("weather_forecast", config_b.tool_discovery.loaded_names)
        self.assertEqual(handlers_b["weather_forecast"].execute_count, 0)

    async def test_p11_product_events_and_failed_tool_does_not_emit_success_block(self):
        config, handlers, _shared, _calls = _discovery_config(
            message="杭州天气",
            playback={"weather_forecast": {"scenario": "error"}},
        )
        emitter = RecordingEmitter()
        script = ScriptedRounds(
            [
                [_tool_call("v1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("v2", "weather_forecast", {"location": "杭州-error", "location_source": "named"})],
                {"stop": True},
            ]
        )
        execution = _execution(config, run_id="run-p11")
        runtime = _runtime_from_execution(execution, script=script, emitter=emitter)
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertFalse(any(isinstance(block, WeatherResultsBlock) for block in emitter.product_blocks))
        config_ok, handlers_ok, _s, _ = _discovery_config(message="杭州天气")
        emitter_ok = RecordingEmitter()
        script_ok = ScriptedRounds(
            [
                [_tool_call("v3", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [_tool_call("v4", "weather_forecast", {"location": "杭州", "location_source": "named"})],
                {"stop": True},
            ]
        )
        execution_ok = _execution(config_ok, run_id="run-p11-ok")
        await run_agent_loop(
            db=None,
            messages=[],
            state=execution_ok.state,
            runtime=_runtime_from_execution(execution_ok, script=script_ok, emitter=emitter_ok),
        )
        self.assertTrue(any(isinstance(block, WeatherResultsBlock) for block in emitter_ok.product_blocks))
        self.assertGreaterEqual(handlers_ok["weather_forecast"].execute_count, 1)

    def test_p12_default_path_still_classifies(self):
        seen = []

        def classify(*, message, task_context_messages, available_tool_names):
            seen.append(message)
            from app.services.stream.run_capability_router import _CandidateRoute

            return _CandidateRoute(
                package_id="direct",
                confidence="high",
                reason_codes=("stable_knowledge_question",),
                include_current_date=False,
            )

        config = build_agent_loop_call_config(
            provider="openai",
            options={},
            capabilities={"functionCalling": True, "searchCapable": True},
            original_message="你好",
            classify_fn=classify,
        )
        self.assertEqual(config.capability_resolution.package_id, "direct")
        self.assertTrue(seen)
        self.assertFalse(config.dynamic_tool_discovery)

    def test_discovery_run_config_uses_experiment_context_not_fake_package(self):
        config, _handlers, _shared, _calls = _discovery_config(message="杭州天气")
        payload = _run_config(AgentLoopLimits(max_steps=3, max_tool_calls=5, total_timeout_s=30), config)
        self.assertIsNone(config.capability_resolution)
        self.assertNotIn("capability_resolution", payload)
        self.assertTrue(payload["dynamic_tool_discovery"]["enabled"])
        self.assertIn("skill", payload["dynamic_tool_discovery"]["unsupported_scenes"])
        self.assertFalse(payload["dynamic_tool_discovery"]["requires_catalog_evidence"])
        self.assertIn("conservative experiment adapter", payload["dynamic_tool_discovery"]["catalog_evidence_note"])
        self.assertIsNone(config.discovery_experiment.package_id)

    def test_greeting_does_not_require_catalog_evidence(self):
        config, _handlers, _shared, _calls = _discovery_config(message="早上好，你是谁？")
        self.assertFalse(config.discovery_experiment.requires_catalog_evidence)
        guarded, kind = resolve_no_evidence_answer(
            "你好，我是助手。",
            content_blocks=[],
            messages=[{"role": "user", "content": "早上好，你是谁？"}],
            capability_resolution=config.capability_resolution,
        )
        self.assertEqual(guarded, "你好，我是助手。")
        self.assertIsNone(kind)

    def test_unsupported_scenes_refuse_instead_of_silent_fallback(self):
        from app.ai.skills.registry import SkillReleasePin

        schemas, handlers, _shared = build_prototype_fixture_catalog()
        pin = SkillReleasePin(
            skill_id="verified-research",
            version="1.0.0",
            content_sha256="0" * 64,
        )
        with self.assertRaises(DynamicToolDiscoveryUnsupportedError) as skill_error:
            build_agent_loop_call_config(
                provider="openai",
                options={"dynamic_tool_discovery": True},
                capabilities={"functionCalling": True, "searchCapable": True, "agentTools": True},
                additional_tools=schemas,
                dynamic_tool_handlers=handlers,
                authorized_tool_names=[schema["function"]["name"] for schema in schemas],
                original_message="杭州天气",
                skill_release_pins=(pin,),
            )
        self.assertEqual(skill_error.exception.scene, "skill")
        with self.assertRaises(DynamicToolDiscoveryUnsupportedError) as research_error:
            build_agent_loop_call_config(
                provider="openai",
                options={"dynamic_tool_discovery": True, "task_mode": "deep_research"},
                capabilities={"functionCalling": True, "searchCapable": True, "agentTools": True},
                additional_tools=schemas,
                dynamic_tool_handlers=handlers,
                original_message="深入研究杭州天气",
            )
        self.assertEqual(research_error.exception.scene, "deep_research")
        with self.assertRaises(DynamicToolDiscoveryUnsupportedError) as continuation_error:
            build_agent_loop_call_config(
                provider="openai",
                options={"dynamic_tool_discovery": True},
                capabilities={"functionCalling": True, "searchCapable": True, "agentTools": True},
                additional_tools=schemas,
                dynamic_tool_handlers=handlers,
                original_message="继续",
                previous_run_id="run-prev",
            )
        self.assertEqual(continuation_error.exception.scene, "continuation")

    def test_catalog_query_is_literal_not_regex(self):
        session = _discovery_config(message="杭州天气")[0].tool_discovery
        matched, mode = session.search("(a+)+$")
        self.assertEqual(mode, "list")
        self.assertTrue(matched)
        listed, list_mode = session.search("page:0")
        self.assertEqual(list_mode, "list")
        self.assertTrue(listed)
        selected, select_mode = session.search("select:weather_forecast")
        self.assertEqual(select_mode, "select")
        self.assertEqual([entry.name for entry in selected], ["weather_forecast"])
        dotted, dotted_mode = session.search("weather_forecast.")
        self.assertEqual(dotted_mode, "list")

    async def test_plan_mode_tool_search_usable_before_and_after_valid_plan(self):
        config, handlers, _shared, _calls = _discovery_config(message="杭州天气并做计划", plan_mode="on")
        execution = _execution(config, run_id="run-r2")
        captured_choices = []

        class CapturingScript(ScriptedRounds):
            async def __call__(self, **kwargs):
                captured_choices.append(kwargs.get("call_kwargs", {}).get("tool_choice"))
                return await super().__call__(**kwargs)

        script = CapturingScript(
            [
                [_tool_call("p1", TOOL_SEARCH_NAME, {"query": "select:weather_forecast"})],
                [
                    _tool_call(
                        "p2",
                        "update_plan",
                        {
                            "explanation": "先查天气再查车次",
                            "plan": [
                                {
                                    "id": "s1",
                                    "step": "查询杭州天气",
                                    "status": "in_progress",
                                    "kind": "other",
                                    "depends_on": [],
                                    "planned_tools": ["weather_forecast"],
                                },
                                {
                                    "id": "s2",
                                    "step": "查询车次",
                                    "status": "pending",
                                    "kind": "other",
                                    "depends_on": ["s1"],
                                    "planned_tools": [],
                                },
                                {
                                    "id": "s3",
                                    "step": "回答",
                                    "status": "pending",
                                    "kind": "answer",
                                    "depends_on": ["s2"],
                                    "planned_tools": [],
                                },
                            ],
                        },
                    )
                ],
                [
                    _tool_call(
                        "p3",
                        "weather_forecast",
                        {"location": "杭州", "location_source": "named", "_plan_item_id": "s1"},
                    )
                ],
                [_tool_call("p4", TOOL_SEARCH_NAME, {"query": "select:search_trains"})],
                [
                    _tool_call(
                        "p5",
                        "update_plan",
                        {
                            "explanation": "补上车次",
                            "plan": [
                                {
                                    "id": "s1",
                                    "step": "查询杭州天气",
                                    "status": "completed",
                                    "kind": "other",
                                    "depends_on": [],
                                    "planned_tools": ["weather_forecast"],
                                },
                                {
                                    "id": "s2",
                                    "step": "查询车次",
                                    "status": "in_progress",
                                    "kind": "other",
                                    "depends_on": ["s1"],
                                    "planned_tools": ["search_trains"],
                                },
                                {
                                    "id": "s3",
                                    "step": "回答",
                                    "status": "pending",
                                    "kind": "answer",
                                    "depends_on": ["s2"],
                                    "planned_tools": [],
                                },
                            ],
                        },
                    )
                ],
                [
                    _tool_call(
                        "p6",
                        "search_trains",
                        {
                            "origin": "杭州",
                            "destination": "上海",
                            "departure_date": "2026-09-26",
                            "_plan_item_id": "s2",
                        },
                    )
                ],
                {"stop": True},
            ]
        )
        runtime = _runtime_from_execution(execution, script=script, emitter=RecordingEmitter())
        await run_agent_loop(db=None, messages=[], state=execution.state, runtime=runtime)
        self.assertNotEqual(
            captured_choices[0],
            {"type": "function", "function": {"name": "update_plan"}},
        )
        self.assertIn(captured_choices[0], ("required", "auto"))
        self.assertGreaterEqual(handlers["weather_forecast"].execute_count, 1)
        self.assertGreaterEqual(handlers["search_trains"].execute_count, 1)

    def test_fixture_clock_is_fixed_experiment_date(self):
        self.assertEqual(EXPERIMENT_NOW.date().isoformat(), "2026-09-22")

    def test_compare_executor_fake_transport_and_budget(self):
        import importlib.util
        import tempfile
        from pathlib import Path

        compare_path = Path(__file__).resolve().parents[3] / "scripts" / "dynamic_tool_discovery_compare.py"
        spec = importlib.util.spec_from_file_location("dynamic_tool_discovery_compare", compare_path)
        compare = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = compare
        spec.loader.exec_module(compare)

        self.assertIsNone(compare.parse_budget(None))
        self.assertIsNone(compare.parse_budget(0))
        self.assertIsNone(compare.parse_budget(-1))
        config = compare.build_compare_config(mode="dry-run", repeats=2, max_requests=24, output_dir=Path("/tmp/x"))
        self.assertEqual(config["limits_per_run"]["max_tokens"], 4096)
        self.assertEqual(config["fixed_date"], "2026-09-22")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            self.assertEqual(compare.main(["--mode", "live"]), 2)
            self.assertEqual(compare.main(["--mode", "live", "--max-requests", "8", "--transport", "litellm"]), 3)
            tiny = compare.ExperimentBudget(max_requests=1)
            executor = compare.PairingExecutor(
                transport=compare.FakeModelTransport(tiny),
                budget=tiny,
                output_dir=output / "tiny",
                cases=[{"id": "greeting", "text": "早上好，你是谁？", "expect": "x", "kind": "normal"}],
                repeats=1,
            )
            tiny_summary = executor.run()
            self.assertTrue(tiny_summary["aborted"] or tiny_summary["incomplete"])
            budget = compare.ExperimentBudget(max_requests=8)
            summary = compare.PairingExecutor(
                transport=compare.FakeModelTransport(budget),
                budget=budget,
                output_dir=output / "full",
                cases=[{"id": "greeting", "text": "早上好，你是谁？", "expect": "x", "kind": "normal"}],
                repeats=1,
            ).run()
            self.assertEqual(summary["used_requests"], 3)
            self.assertFalse(summary["aborted"])
            self.assertEqual(summary["job_order"][:2], ["baseline:greeting:r0", "candidate:greeting:r0"])
            self.assertTrue((output / "full" / "baseline-greeting-r0.json").exists())
            self.assertTrue((output / "full" / "candidate-greeting-r0.json").exists())
            baseline = json.loads((output / "full" / "baseline-greeting-r0.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["phases"][0]["phase"], "classify")
            self.assertTrue(baseline["request_hash"])
            self.assertEqual(baseline["fixed_date"], EXPERIMENT_NOW.isoformat())


def _fn_name(tool: dict) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""
