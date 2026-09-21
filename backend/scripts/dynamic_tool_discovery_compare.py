#!/usr/bin/env python3
"""动态工具发现对照入口：默认离线，dry-run 打印计划，live 需显式限额。

两臂走同一套 Fusion 装配与 Agent 循环，只替换模型传输、假产品工具和隔离存储。
不因环境中存在密钥就联网，不打印凭据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

EXPERIMENT_TZ = timezone(timedelta(hours=8))
EXPERIMENT_NOW = datetime(2026, 9, 22, 9, 0, tzinfo=EXPERIMENT_TZ)

DEFAULT_OUTPUT = _BACKEND_ROOT / "tmp" / "dynamic-tool-discovery"
LIMITS_PER_RUN = {
    "max_steps": 8,
    "max_tool_calls": 12,
    "max_tokens": 4096,
}
CASES = [
    {
        "id": "greeting",
        "text": "早上好，你是谁？",
        "expect": "问候/身份，不必调用产品工具",
        "kind": "normal",
    },
    {
        "id": "weather_only",
        "text": "帮我看看杭州这周末天气怎么样",
        "expect": "单一天气查询",
        "kind": "normal",
    },
    {
        "id": "weather_and_train",
        "text": "查一下杭州周末天气，再找上海过去的高铁。",
        "expect": "天气加车次，候选路径应发现两类工具",
        "kind": "pressure_hidden_tools",
    },
    {
        "id": "tool_failure_fallback",
        "text": "先查杭州天气，要是天气接口不行就改用网页搜一下",
        "expect": "工具失败后改用授权目录内替代工具",
        "kind": "normal",
    },
    {
        "id": "no_network",
        "text": "本次不要联网，只用你已经知道的说杭州周末适不适合出门",
        "expect": "明确禁网，发现和执行都不能绕过",
        "kind": "normal",
    },
    {
        "id": "empty_then_specific",
        "text": "杭州到上海周六高铁有哪些，给我具体车次和票价",
        "expect": "空结果下索要具体班次，不得把合成空账本当已验证事实",
        "kind": "normal",
    },
]
CASE_PLAYBACK = {
    "tool_failure_fallback": {"weather_forecast": {"scenario": "error"}},
    "empty_then_specific": {"search_trains": {"scenario": "empty"}},
}
CASE_GOAL_TOOLS = {
    "greeting": (),
    "weather_only": ("weather_forecast",),
    "weather_and_train": ("weather_forecast", "search_trains"),
    "tool_failure_fallback": ("weather_forecast", "web_search"),
    "no_network": (),
    "empty_then_specific": ("search_trains",),
}


class ExperimentBudgetExhausted(RuntimeError):
    """全局实验请求额度用尽，尚未调用传输层。"""


class RealModelSendDenied(RuntimeError):
    """未显式授权真实模型发送。"""


class ModelTransport(Protocol):
    def complete(self, request: "ModelRequest") -> Any: ...


@dataclass(frozen=True)
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[str]
    tool_choice: Any
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    classify: bool = False
    max_tokens: int | None = None
    case_id: str | None = None
    arm: str | None = None
    repeat: int | None = None
    phase: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    status: str
    request_hash: str
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class ExperimentBudget:
    max_requests: int
    max_tokens: int = LIMITS_PER_RUN["max_tokens"]
    used_requests: int = 0
    used_tokens: int = 0
    aborted: bool = False

    def consume(self, *, requests: int = 1, tokens: int = 0) -> bool:
        if self.aborted:
            return False
        if self.used_requests + requests > self.max_requests:
            self.aborted = True
            return False
        if self.max_tokens and tokens and self.used_tokens + tokens > self.max_tokens:
            self.aborted = True
            return False
        self.used_requests += requests
        self.used_tokens += tokens
        return True


def content_request_hash(*, messages: list[Any], tools: Any, tool_choice: Any, max_tokens: int | None = None) -> str:
    payload = {
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": max_tokens,
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def request_hash(request: ModelRequest) -> str:
    """真实请求内容 hash，不含 arm/repeat 等实验元数据。"""

    return content_request_hash(
        messages=list(request.messages),
        tools=list(request.tool_schemas or request.tools),
        tool_choice=request.tool_choice,
        max_tokens=request.max_tokens,
    )


def _serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages or []:
        if hasattr(message, "role"):
            serialized.append(
                {
                    "role": message.role,
                    "content": message.content,
                    "section_id": getattr(message, "section_id", None),
                }
            )
        elif isinstance(message, dict):
            serialized.append(
                {
                    "role": message.get("role"),
                    "content": message.get("content"),
                }
            )
        else:
            serialized.append({"content": str(message)})
    return serialized


def _tool_names(schemas: list[Any] | None) -> list[str]:
    names: list[str] = []
    for tool in schemas or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if name:
            names.append(str(name))
    return names


def _forced_tool_name(tool_choice: Any) -> str | None:
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
        name = function.get("name")
        return str(name) if name else None
    return None


def _transcript(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, ensure_ascii=False, default=str)


class FakeModelTransport:
    """按可见 schema 与工具回执生成可执行调用序列，不连接真实模型，不自行扣预算。"""

    def __init__(self, budget: ExperimentBudget | None = None) -> None:
        del budget
        self.calls: list[dict[str, Any]] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        digest = request_hash(request)
        names = set(request.tools)
        forced = _forced_tool_name(request.tool_choice)
        blob = _transcript(list(request.messages))
        case_id = request.case_id or "weather_only"
        goals = CASE_GOAL_TOOLS.get(case_id, ("weather_forecast",))
        response = self._decide(names=names, forced=forced, blob=blob, goals=goals, digest=digest, case_id=case_id)
        self.calls.append({"request": asdict(request), "response": asdict(response)})
        return response

    def _decide(
        self,
        *,
        names: set[str],
        forced: str | None,
        blob: str,
        goals: tuple[str, ...],
        digest: str,
        case_id: str,
    ) -> ModelResponse:
        def emit(tool_name: str, arguments: dict[str, Any], call_id: str) -> ModelResponse:
            if forced is not None and tool_name != forced:
                return ModelResponse(status="ok", request_hash=digest, content="无法在当前工具选择下调用其他工具")
            if tool_name not in names:
                return ModelResponse(status="ok", request_hash=digest, content="目标工具本轮不可见")
            return ModelResponse(
                status="ok",
                request_hash=digest,
                tool_calls=[{"id": call_id, "name": tool_name, "arguments": arguments}],
                input_tokens=4,
                output_tokens=8,
            )

        if case_id == "greeting":
            return ModelResponse(
                status="ok",
                request_hash=digest,
                content="你好，我是助手。",
                input_tokens=4,
                output_tokens=8,
            )
        if case_id == "no_network" and "tool_search" in names and "web_search" not in names:
            if "tool_search" not in blob:
                return emit("tool_search", {"query": "select:web_search,weather_forecast"}, "discover_denied")
            return ModelResponse(
                status="ok",
                request_hash=digest,
                content="按你的要求，这次不联网查询。",
                input_tokens=4,
                output_tokens=8,
            )
        pending = [name for name in goals if name not in names]
        if pending and "tool_search" in names:
            query = "select:" + ",".join(pending)
            return emit("tool_search", {"query": query}, f"discover_{pending[0]}")
        if "weather_forecast" in goals and "weather_forecast" in names and "day_weather" not in blob:
            return emit(
                "weather_forecast",
                {"location": "杭州", "location_source": "named"},
                "call_weather",
            )
        if (
            "search_trains" in goals
            and "search_trains" in names
            and "syn-g7301" not in blob
            and "无合成班次" not in blob
        ):
            destination = "empty" if case_id == "empty_then_specific" else "上海"
            return emit(
                "search_trains",
                {"origin": "杭州", "destination": destination, "departure_date": "2026-09-26"},
                "call_trains",
            )
        if "web_search" in goals and "web_search" in names and "杭州周末天气（合成）" not in blob:
            if "weather_unavailable" in blob or "forecast_days" in blob or "weather_forecast" in blob:
                return emit("web_search", {"query": "杭州天气"}, "call_search")
        snippet = blob[-240:] if blob else ""
        return ModelResponse(
            status="ok",
            request_hash=digest,
            content="已根据工具结果作答。" + snippet[:80],
            input_tokens=4,
            output_tokens=8,
        )


class LiteLLMProxyTransport:
    """LiteLLM Proxy alias 适配。默认不发送；测试可注入 send_fn。"""

    def __init__(
        self,
        *,
        send_fn: Any | None = None,
        allow_real: bool = False,
        alias: str | None = None,
    ) -> None:
        self.send_fn = send_fn
        self.allow_real = allow_real
        self.alias = alias or os.environ.get("FUSION_COMPARE_MODEL_ALIAS", "fusion-main")
        self.send_calls: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        digest = request_hash(request)
        payload = {
            "model": self.alias,
            "messages": list(request.messages),
            "tools": list(request.tool_schemas),
            "tool_choice": request.tool_choice,
            "max_tokens": request.max_tokens or LIMITS_PER_RUN["max_tokens"],
            "stream": False,
        }
        if self.send_fn is None and not self.allow_real:
            raise RealModelSendDenied("未设置 FUSION_COMPARE_ALLOW_REAL_LLM，拒绝真实发送")
        self.send_calls.append(payload)
        if self.send_fn is not None:
            result = self.send_fn(payload)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, ModelResponse):
                response = result
            elif isinstance(result, dict):
                response = ModelResponse(
                    status="ok",
                    request_hash=digest,
                    content=str(result.get("content") or ""),
                    tool_calls=list(result.get("tool_calls") or []),
                    input_tokens=int(result.get("input_tokens") or 0),
                    output_tokens=int(result.get("output_tokens") or 0),
                )
            else:
                response = ModelResponse(status="ok", request_hash=digest, content=str(result or ""))
            self.calls.append({"request": asdict(request), "response": asdict(response)})
            return response
        import litellm

        completion = await litellm.acompletion(**payload)
        message = completion.choices[0].message
        tool_calls = []
        for item in getattr(message, "tool_calls", None) or []:
            function = item.function
            arguments = function.arguments
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            tool_calls.append({"id": item.id, "name": function.name, "arguments": arguments})
        usage = getattr(completion, "usage", None)
        response = ModelResponse(
            status="ok",
            request_hash=digest,
            content=str(getattr(message, "content", "") or ""),
            tool_calls=tool_calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        self.calls.append({"request": asdict(request), "response": asdict(response)})
        return response


class PairingExecutor:
    def __init__(
        self,
        *,
        transport: Any,
        budget: ExperimentBudget,
        output_dir: Path,
        cases: list[dict[str, Any]] | None = None,
        repeats: int = 2,
        break_discovery: bool = False,
    ) -> None:
        self.transport = transport
        self.budget = budget
        self.output_dir = output_dir
        self.cases = cases or CASES
        self.repeats = repeats
        self.break_discovery = break_discovery
        self.spies: dict[str, list[Any]] = {
            "assemble": [],
            "driver": [],
            "classify_route": [],
            "llm_sends": [],
        }

    def jobs(self) -> list[tuple[str, dict[str, Any], int]]:
        ordered: list[tuple[str, dict[str, Any], int]] = []
        for repeat in range(self.repeats):
            for case in self.cases:
                ordered.append(("baseline", case, repeat))
                ordered.append(("candidate", case, repeat))
        return ordered

    def run(self) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async())
        raise RuntimeError("事件循环已在运行时请调用 PairingExecutor.run_async()")

    async def run_async(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        completed: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        pair_status: dict[tuple[str, int], dict[str, str]] = {}
        for arm, case, repeat in self.jobs():
            if self.budget.aborted:
                record = {
                    "status": "budget_aborted",
                    "arm": arm,
                    "case_id": case["id"],
                    "repeat": repeat,
                    "reason": "budget_aborted",
                    "transport_ok": False,
                    "task_completed": False,
                    "final_output": None,
                }
                incomplete.append(record)
                pair_status.setdefault((case["id"], repeat), {})[arm] = "incomplete"
                continue
            record = await self._run_arm(arm=arm, case=case, repeat=repeat)
            path = self.output_dir / f"{arm}-{case['id']}-r{repeat}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            record["output_path"] = str(path)
            if record.get("status") == "ok" and record.get("task_completed"):
                completed.append(record)
                pair_status.setdefault((case["id"], repeat), {})[arm] = "complete"
            else:
                incomplete.append(record)
                pair_status.setdefault((case["id"], repeat), {})[arm] = "incomplete"
        incomplete_pairs = [
            {"case_id": case_id, "repeat": repeat, "arms": arms}
            for (case_id, repeat), arms in pair_status.items()
            if set(arms.values()) != {"complete"} or len(arms) < 2
        ]
        summary = {
            "mode": "paired-fusion-loop",
            "fixed_date": EXPERIMENT_NOW.date().isoformat(),
            "timezone": "Asia/Shanghai",
            "limits_per_run": LIMITS_PER_RUN,
            "max_requests": self.budget.max_requests,
            "used_requests": self.budget.used_requests,
            "used_tokens": self.budget.used_tokens,
            "aborted": self.budget.aborted,
            "completed": completed,
            "incomplete": incomplete,
            "incomplete_pairs": incomplete_pairs,
            "job_order": [f"{arm}:{case['id']}:r{repeat}" for arm, case, repeat in self.jobs()],
            "base": _git_sha("HEAD"),
            "live_executed": False,
            "live_real_model": False,
            "cache_status": "未知",
            "spies": {key: list(value) for key, value in self.spies.items()},
        }
        (self.output_dir / "pairing-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return summary

    async def _run_arm(self, *, arm: str, case: dict[str, Any], repeat: int) -> dict[str, Any]:
        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
        from unittest.mock import patch

        from app.schemas.chat import Usage
        from app.services.stream.agent_loop_driver import run_agent_loop
        from app.services.stream.agent_loop_execution import (
            AgentLoopDependencies,
            AgentLoopExecutionRequest,
            build_agent_loop_execution,
        )
        from app.services.stream.agent_loop_policy import AgentLoopLimits
        from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
        from app.services.stream.agent_round import AgentRoundResult
        from app.services.stream.dynamic_tool_discovery_fixtures import build_prototype_fixture_catalog
        from app.services.stream.limit_summary import LimitSummaryOutcome
        from app.services.stream.run_capability_router import resolve_run_capability_route
        from app.services.stream.step_lifecycle import AgentStepContext
        from app.services.stream.tool_execution_result import ToolExecutionRecord
        from app.services.stream.tool_round import handle_tool_calls_round

        schemas, handlers, _shared = build_prototype_fixture_catalog(playback=CASE_PLAYBACK.get(case["id"]))
        authorized = [schema["function"]["name"] for schema in schemas]
        options: dict[str, Any] = {"max_tokens": LIMITS_PER_RUN["max_tokens"], "plan_mode": "off"}
        if arm == "candidate":
            options["dynamic_tool_discovery"] = True
        route_calls: list[Any] = []

        def wrapped_route(**kwargs):
            route_calls.append(kwargs)
            self.spies["classify_route"].append({"arm": arm, "case_id": case["id"]})
            return resolve_run_capability_route(**kwargs)

        self.spies["assemble"].append({"arm": arm, "case_id": case["id"]})
        with patch(
            "app.services.stream.agent_loop_request_prep.resolve_run_capability_route",
            side_effect=wrapped_route,
        ):
            config = build_agent_loop_call_config(
                provider="openai",
                options=options,
                capabilities={"functionCalling": True, "searchCapable": True, "agentTools": True},
                additional_tools=schemas,
                dynamic_tool_handlers=handlers,
                authorized_tool_names=authorized,
                original_message=case["text"],
            )
        if arm == "candidate" and route_calls:
            raise AssertionError("候选发现路径不得调用包分类路由")
        if self.break_discovery and config.tool_discovery is not None:
            handler = config.dynamic_tool_handlers.get("tool_search")

            async def _fail(_args):
                raise RuntimeError("injected discovery failure")

            if handler is not None:
                handler.execute = _fail  # type: ignore[method-assign]

        rounds: list[dict[str, Any]] = []
        budget = self.budget
        transport = self.transport
        spies = self.spies

        async def llm_call_fn(_model, _kwargs, messages, **call_kwargs):
            if not budget.consume(requests=1, tokens=0):
                raise ExperimentBudgetExhausted("experiment_request_cap")
            schemas_this_round = list(call_kwargs.get("tools") or [])
            request = ModelRequest(
                messages=_serialize_messages(messages),
                tools=_tool_names(schemas_this_round),
                tool_choice=call_kwargs.get("tool_choice"),
                tool_schemas=schemas_this_round,
                classify=False,
                max_tokens=call_kwargs.get("max_tokens") or LIMITS_PER_RUN["max_tokens"],
                case_id=case["id"],
                arm=arm,
                repeat=repeat,
                phase="llm",
            )
            spies["llm_sends"].append(
                {
                    "arm": arm,
                    "case_id": case["id"],
                    "tools": list(request.tools),
                    "request_hash": request_hash(request),
                }
            )
            result = transport.complete(request)
            if inspect.isawaitable(result):
                result = await result
            rounds.append(
                {
                    "visible_tools": list(request.tools),
                    "tool_choice": request.tool_choice,
                    "request_hash": result.request_hash,
                    "tool_calls": result.tool_calls,
                    "content": result.content,
                    "messages": request.messages,
                }
            )
            return result

        async def run_round_fn(**kwargs):
            tools = kwargs.get("call_kwargs", {}).get("tools") or []
            names = _tool_names(tools)
            response = await llm_call_fn(
                kwargs.get("litellm_model"),
                kwargs.get("litellm_kwargs") or {},
                kwargs.get("messages") or [],
                **(kwargs.get("call_kwargs") or {}),
            )
            if response.status != "ok":
                raise RuntimeError(response.error or response.status)
            finish = "tool_calls" if response.tool_calls else "stop"
            return AgentRoundResult(
                reasoning_buf="",
                content_buf=response.content,
                tool_calls=response.tool_calls,
                finish_reason=finish,
                accumulated_usage=Usage(
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                ),
                announced_tool_names=frozenset(names),
            )

        async def start_step(**kwargs):
            step_number = kwargs["step_number"]
            return AgentStepContext(
                step_id=f"step-{step_number}",
                step_number=step_number,
                started_at=0.0,
                thinking_block_id=f"th-{step_number}",
                text_block_id=f"tx-{step_number}",
            )

        async def complete_step(**_kwargs):
            return None

        async def execute_tools(tool_calls, conversation_id, user_id, model_id, provider, **kwargs):
            del conversation_id, user_id, model_id, provider
            records = []
            bound = kwargs.get("tool_handlers") or config.dynamic_tool_handlers or handlers
            for tool_call in tool_calls or []:
                handler = bound[tool_call["name"]]
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

        async def limit_summary(**_kwargs):
            return LimitSummaryOutcome(
                accumulated_usage=Usage(input_tokens=0, output_tokens=0),
                context=None,
                incomplete=False,
            )

        class RecordingEmitter:
            def __init__(self) -> None:
                self.product_blocks: list[object] = []
                self.tool_events: list[tuple[str, str]] = []

            async def content_block_upserted(self, **kwargs):
                self.product_blocks.append(kwargs.get("content_block"))

            async def tool_call_started(self, **kwargs):
                self.tool_events.append(("started", str(kwargs.get("tool_name"))))

            async def tool_call_completed(self, **kwargs):
                self.tool_events.append(("completed", str(kwargs.get("tool_name"))))

            def __getattr__(self, name: str):
                async def _noop(**_kwargs):
                    return None

                return _noop

        execution = build_agent_loop_execution(
            request=AgentLoopExecutionRequest(
                db=None,
                conversation_id=f"conv-{arm}-{case['id']}-{repeat}",
                user_id="compare-user",
                model_id="fusion-main",
                litellm_model="openai/fusion-main",
                litellm_kwargs={},
                provider="openai",
                assistant_message_id=f"msg-{arm}-{repeat}",
                task_id=f"task-{arm}-{repeat}",
                call_config=config,
                trace_id=f"run-{arm}-{case['id']}-r{repeat}",
                original_message=case["text"],
            ),
            limits=AgentLoopLimits(
                max_steps=LIMITS_PER_RUN["max_steps"],
                max_tool_calls=LIMITS_PER_RUN["max_tool_calls"],
                total_timeout_s=300,
            ),
            dependencies=AgentLoopDependencies(
                session_cache=SimpleNamespace(
                    write_step_started=lambda **_k: asyncio.sleep(0),
                    write_step_completed=lambda **_k: asyncio.sleep(0),
                ),
                redis_writer=object(),
                start_step_fn=start_step,
                complete_step_fn=complete_step,
                run_round_fn=run_round_fn,
                handle_tool_calls_round_fn=handle_tool_calls_round,
                run_limit_summary_step_fn=limit_summary,
                llm_call_fn=llm_call_fn,
                stream_round_fn=_unused_stream,
                execute_tools_fn=execute_tools,
                persist_message_fn=lambda *_a, **_k: None,
                log_round_summary_fn=lambda **_k: None,
                warning_fn=lambda _m: None,
                clock=lambda: 1.0,
            ),
        )
        emitter = RecordingEmitter()
        runtime = execution.runtime
        object.__setattr__(runtime, "run_round_fn", run_round_fn)
        object.__setattr__(runtime, "handle_tool_calls_round_fn", handle_tool_calls_round)
        object.__setattr__(runtime, "execute_tools_fn", execute_tools)
        object.__setattr__(runtime, "llm_call_fn", llm_call_fn)
        object.__setattr__(runtime, "start_step_fn", start_step)
        object.__setattr__(runtime, "complete_step_fn", complete_step)
        object.__setattr__(runtime, "persist_message_fn", lambda *_a, **_k: None)
        object.__setattr__(runtime, "run_limit_summary_step_fn", limit_summary)
        object.__setattr__(runtime, "tool_discovery", execution.state.tool_discovery)
        object.__setattr__(runtime, "emitter", emitter)
        self.spies["driver"].append({"arm": arm, "case_id": case["id"]})
        transport_ok = True
        error = None
        try:
            messages: list = []
            await run_agent_loop(db=None, messages=messages, state=execution.state, runtime=runtime)
        except ExperimentBudgetExhausted as exc:
            transport_ok = False
            error = str(exc)
        except RealModelSendDenied as exc:
            transport_ok = False
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 对照记录需要保留失败形态
            transport_ok = False
            error = f"{type(exc).__name__}: {exc}"
        handler_counts = {name: getattr(item, "execute_count", 0) for name, item in handlers.items()}
        discovery_events = list(getattr(config.tool_discovery, "events", []) or [])
        final_output = ""
        if rounds:
            final_output = str(rounds[-1].get("content") or "")
        weather_schema_seen = any("weather_forecast" in (round_row.get("visible_tools") or []) for round_row in rounds)
        result_in_messages = any(
            "day_weather" in json.dumps(round_row.get("messages") or [], ensure_ascii=False, default=str)
            for round_row in rounds[1:]
        )
        task_completed = bool(transport_ok and error is None and final_output and not self.break_discovery)
        if self.break_discovery:
            task_completed = False
        return {
            "status": "ok" if transport_ok and error is None else "error",
            "arm": arm,
            "case_id": case["id"],
            "repeat": repeat,
            "input_hash": _case_hash(case),
            "request_hash": rounds[-1]["request_hash"] if rounds else None,
            "rounds": rounds,
            "phases": rounds,
            "final_output": final_output,
            "transport_ok": transport_ok,
            "task_completed": task_completed,
            "error": error,
            "handler_counts": handler_counts,
            "discovery_events": discovery_events,
            "weather_schema_seen": weather_schema_seen,
            "weather_result_in_later_messages": result_in_messages,
            "classify_route_calls": len(route_calls),
            "package_id": getattr(config.capability_resolution, "package_id", None),
            "dynamic_tool_discovery": bool(config.dynamic_tool_discovery),
            "fixed_date": EXPERIMENT_NOW.isoformat(),
            "fixture_clock": EXPERIMENT_NOW.isoformat(),
            "limits_per_run": LIMITS_PER_RUN,
        }


async def _unused_stream(*_args, **_kwargs):
    raise AssertionError("配对路径应通过注入的 run_round_fn / llm_call_fn，不走真实 stream")


def build_compare_config(*, mode: str, repeats: int, max_requests: int | None, output_dir: Path) -> dict[str, Any]:
    alias = os.environ.get("FUSION_COMPARE_MODEL_ALIAS", "fusion-main")
    return {
        "mode": mode,
        "model_alias": alias,
        "case_count": len(CASES),
        "repeats_per_arm": repeats,
        "paths": ["baseline_package", "candidate_discovery"],
        "limits_per_run": LIMITS_PER_RUN,
        "experiment_request_cap": max_requests,
        "output_dir": str(output_dir),
        "cache_status": "未知",
        "timezone": "Asia/Shanghai",
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
        "cases": [{**case, "input_hash": _case_hash(case)} for case in CASES],
        "live_will_not_start_from_env_secrets": True,
    }


def _git_sha(kind: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", kind],
            cwd=_BACKEND_ROOT.parent,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _print_plan(config: dict[str, Any]) -> None:
    print("dynamic_tool_discovery_compare")
    print(f"mode={config['mode']}")
    print(f"model_alias={config['model_alias']} (不输出凭据)")
    print(f"case_count={config['case_count']}")
    print(f"repeats_per_arm={config['repeats_per_arm']}")
    print("paths=baseline_package,candidate_discovery")
    limits = config["limits_per_run"]
    print(
        "limits_per_run: "
        f"max_steps={limits['max_steps']} max_tool_calls={limits['max_tool_calls']} max_tokens={limits['max_tokens']}"
    )
    cap = config["experiment_request_cap"]
    print(f"experiment_request_cap={cap if cap is not None else 'unset'}")
    print(f"output_dir={config['output_dir']}")
    print(f"cache_status={config['cache_status']}")
    print(f"timezone={config['timezone']}")
    print(f"fixed_date={config['fixed_date']}")
    print("live 不会因环境变量中存在密钥自动启动")
    for case in CASES:
        print(f"case {case['id']}: {case['text']} | expect={case['expect']} | kind={case['kind']}")


def _case_hash(case: dict) -> str:
    payload = json.dumps({"id": case["id"], "text": case["text"]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def run_offline(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND_ROOT)
    env.setdefault("DATABASE_URL", "sqlite:///:memory:")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test/services/stream/test_dynamic_tool_discovery.py",
            "-q",
            "--tb=short",
        ],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    log_path = output_dir / "offline-pytest.log"
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    summary = {
        "mode": "offline",
        "exit_code": result.returncode,
        "base": _git_sha("HEAD"),
        "log": str(log_path),
        "cases": [{**case, "input_hash": _case_hash(case)} for case in CASES],
        "live_executed": False,
        "live_budget": None,
        "live_consumed": 0,
        "fixed_date": EXPERIMENT_NOW.date().isoformat(),
    }
    (output_dir / "offline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
    print(f"offline_log={log_path}")
    return result.returncode


def parse_budget(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value <= 0:
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="动态工具发现基线/候选对照")
    parser.add_argument("--mode", choices=("offline", "dry-run", "live"), default="offline")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--transport", choices=("fake", "litellm"), default="fake")
    args = parser.parse_args(argv)
    config = build_compare_config(
        mode=args.mode,
        repeats=args.repeats,
        max_requests=args.max_requests,
        output_dir=args.output_dir,
    )
    _print_plan(config)
    if args.mode == "dry-run":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "dry-run-config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("dry-run 完成：未调用模型、未消耗额度；以上来自实际对照配置")
        return 0
    if args.mode == "live":
        budget_value = parse_budget(args.max_requests)
        if budget_value is None:
            print("live 缺显式 --max-requests 限额，拒绝运行")
            return 2
        budget = ExperimentBudget(max_requests=budget_value)
        allow_real = os.environ.get("FUSION_COMPARE_ALLOW_REAL_LLM") == "1"
        if args.transport == "litellm":
            transport = LiteLLMProxyTransport(allow_real=allow_real)
        else:
            transport = FakeModelTransport()
        try:
            summary = PairingExecutor(
                transport=transport,
                budget=budget,
                output_dir=args.output_dir / "pairing",
                repeats=args.repeats,
            ).run()
        except RealModelSendDenied:
            print("live LiteLLM 适配已实现，但未授权真实发送；send=0")
            return 3
        print(
            json.dumps(
                {
                    "used_requests": summary["used_requests"],
                    "aborted": summary["aborted"],
                    "incomplete_pairs": len(summary.get("incomplete_pairs") or []),
                },
                ensure_ascii=False,
            )
        )
        print(f"pairing_summary={args.output_dir / 'pairing' / 'pairing-summary.json'}")
        if args.transport == "litellm" and not allow_real and not summary.get("completed"):
            print("live LiteLLM 未真实联网；可用注入 send_fn 验证同一执行器")
            return 3
        return 0
    return run_offline(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
