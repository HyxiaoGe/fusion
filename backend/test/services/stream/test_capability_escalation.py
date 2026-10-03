import asyncio
from dataclasses import dataclass, field

import pytest

from app.ai.prompts.section_ids import TOOL_USAGE_CONTRACT, VISIBLE_RESPONSE_LANGUAGE
from app.schemas.trajectory import TrajectoryCapabilityResolution
from app.services.agent import events as ev
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.agent.trajectory_payload import build_trajectory_payload
from app.services.chat.model_call_language_policy import finalize_model_call_language_policy
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_DEFINITIONS
from app.services.stream.agent_loop_request_prep import (
    announced_tool_names_from_call_kwargs,
    build_agent_loop_call_config,
    prepare_agent_loop_messages,
)
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.capability_escalation import (
    REQUEST_CAPABILITY_TOOL_NAME,
    apply_pending_escalation,
)
from app.services.stream.network_budget import NetworkToolBudget
from app.services.stream.run_capability_router import _CandidateRoute
from app.utils.run_capability_contract import CAPABILITY_PACKAGES
from test.services.stream.test_agent_loop_driver import _runtime

pytestmark = pytest.mark.capability_escalation

CAPABILITIES = {"functionCalling": True, "searchCapable": True, "agentTools": True}
PRODUCT_TOOLS = [*AMAP_PRODUCT_DEFINITIONS, *FLYAI_TRAVEL_DEFINITIONS]
PRODUCT_HANDLERS = {tool["function"]["name"]: object() for tool in PRODUCT_TOOLS}
TRIP_MESSAGE = "周末带孩子在上海玩一天，怎么安排比较好"


def _candidate(package_id: str, **overrides) -> _CandidateRoute:
    spec = CAPABILITY_PACKAGES[package_id]
    values = dict(
        package_id=package_id,
        confidence=spec.confidence_options[0],
        reason_codes=spec.reason_code_options[0],
        include_current_date=True,
        resolution_mode=spec.resolution_mode,
    )
    values.update(overrides)
    return _CandidateRoute(**values)


def _config(candidate: _CandidateRoute, *, options=None, capabilities=CAPABILITIES, **kwargs):
    return build_agent_loop_call_config(
        provider="openai",
        options=options or {},
        capabilities=capabilities,
        additional_tools=PRODUCT_TOOLS,
        dynamic_tool_handlers=dict(PRODUCT_HANDLERS),
        original_message=TRIP_MESSAGE,
        classify_fn=lambda **_kwargs: candidate,
        **kwargs,
    )


def _request(config, **args):
    handler = config.dynamic_tool_handlers[REQUEST_CAPABILITY_TOOL_NAME]
    return asyncio.run(handler.execute(args))


def test_direct_route_offers_request_capability_outside_the_announced_tools():
    config = _config(_candidate("direct"))

    schema_names = announced_tool_names_from_call_kwargs(config.call_kwargs)
    assert REQUEST_CAPABILITY_TOOL_NAME in schema_names
    assert config.announced_tools == []
    assert REQUEST_CAPABILITY_TOOL_NAME in config.unplanned_tool_names
    schema = next(
        tool for tool in config.call_kwargs["tools"] if tool["function"]["name"] == REQUEST_CAPABILITY_TOOL_NAME
    )
    targets = schema["function"]["parameters"]["properties"]["package_id"]["enum"]
    assert {"fresh_web", "weather", "place_discovery", "mixed_itinerary"} <= set(targets)
    assert not {"direct", "clarification_only", "deep_research", "mcp_explicit"} & set(targets)


def test_clarification_route_also_offers_request_capability():
    assert _config(_candidate("clarification_only")).escalation_session is not None


@pytest.mark.parametrize(
    ("candidate", "options", "kwargs"),
    [
        (_candidate("fresh_web"), {}, {}),
        (_candidate("direct", output_mode="document"), {}, {}),
        (_candidate("direct"), {"disable_tools": True}, {}),
        (_candidate("direct"), {"knowledge_grounded": True}, {}),
        (_candidate("direct"), {"stream_mode": "continuation"}, {}),
        (_candidate("direct"), {}, {"previous_run_id": "run-previous"}),
    ],
)
def test_escalation_is_not_offered_outside_phase_one_scope(candidate, options, kwargs):
    config = _config(candidate, options=options, **kwargs)

    assert config.escalation_session is None
    assert REQUEST_CAPABILITY_TOOL_NAME not in announced_tool_names_from_call_kwargs(config.call_kwargs)


def test_escalation_is_not_offered_when_the_switch_is_off(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RUN_CAPABILITY_ESCALATION_ENABLED", False)

    assert _config(_candidate("direct")).escalation_session is None


def test_granted_escalation_matches_a_run_routed_to_the_target_from_the_start():
    source = _config(_candidate("direct"))

    result = _request(
        source,
        package_id="mixed_itinerary",
        tool_names=["weather_forecast", "local_place_search"],
        primary_tool_name="weather_forecast",
        reason="Needs this weekend's forecast and real places in Shanghai.",
    )

    assert result.status == "success"
    escalated = source.escalation_session.pending.config
    direct_target = _config(
        _candidate(
            "mixed_itinerary",
            explicit_tool_names=("weather_forecast", "local_place_search"),
            required_primary_tool_name="weather_forecast",
        )
    )
    assert escalated.capability_resolution == direct_target.capability_resolution
    assert escalated.announced_tools == direct_target.announced_tools
    assert announced_tool_names_from_call_kwargs(escalated.call_kwargs) == announced_tool_names_from_call_kwargs(
        direct_target.call_kwargs
    )
    assert escalated.plan_mode == direct_target.plan_mode
    assert escalated.required_initial_tool_counts == direct_target.required_initial_tool_counts
    assert escalated.escalation_session is None
    assert result.data["tool_names"] == list(escalated.announced_tools)


def test_single_tool_package_can_be_requested_without_listing_tools():
    source = _config(_candidate("direct"))

    result = _request(source, package_id="fresh_web", reason="Needs current news.")

    assert result.status == "success"
    assert source.escalation_session.pending.config.capability_resolution.package_id == "fresh_web"


@pytest.mark.parametrize(
    ("args", "reason_code"),
    [
        ({"package_id": "deep_research", "reason": "x"}, "unknown_package"),
        ({"package_id": "mixed_itinerary", "reason": "x"}, "invalid_tool_selection"),
        (
            {"package_id": "mixed_itinerary", "tool_names": ["weather_forecast", "local_place_search"], "reason": "x"},
            "invalid_tool_selection",
        ),
    ],
)
def test_invalid_requests_are_rejected_without_switching(args, reason_code):
    source = _config(_candidate("direct"))

    result = _request(source, **args)

    assert result.status == "failed"
    assert result.data["reason_code"] == reason_code
    assert source.escalation_session.pending is None


def test_user_network_ban_from_the_first_route_cannot_be_lifted():
    source = _config(_candidate("direct", network_policy="no_network"))

    result = _request(source, package_id="fresh_web", reason="Needs current news.")

    assert result.status == "failed"
    assert result.data["reason_code"] == "required_tools_unavailable"
    assert source.escalation_session.pending is None


def test_only_one_escalation_and_limited_attempts_per_run():
    source = _config(_candidate("direct"))
    assert _request(source, package_id="weather", reason="x").status == "success"

    second = _request(source, package_id="fresh_web", reason="x")
    assert second.data["reason_code"] == "already_escalated"

    exhausted = _config(_candidate("direct"))
    _request(exhausted, package_id="deep_research", reason="x")
    _request(exhausted, package_id="deep_research", reason="x")
    assert _request(exhausted, package_id="weather", reason="x").data["reason_code"] == "request_limit_reached"
    handler = exhausted.dynamic_tool_handlers[REQUEST_CAPABILITY_TOOL_NAME]
    assert asyncio.run(handler.is_run_budget_exhausted())


def test_escalation_is_blocked_once_a_plan_exists():
    source = _config(_candidate("direct"))
    source.escalation_session.blocked_reason = lambda: "plan_already_created"

    result = _request(source, package_id="weather", reason="x")

    assert result.data["reason_code"] == "plan_already_created"


@dataclass
class _RecordingEmitter:
    escalations: list[dict] = field(default_factory=list)

    async def capability_escalated(self, **payload):
        self.escalations.append(payload)


async def _prepared(config):
    prepared = await prepare_agent_loop_messages(
        db=object(),
        user_id="user-1",
        raw_messages=[],
        has_vision=False,
        file_ids=None,
        original_message=TRIP_MESSAGE,
        call_config=config,
        file_repo_factory=lambda _db: object(),
        load_user_system_prompt_fn=lambda _db, _user_id: None,
        preprocess_user_input=False,
    )
    messages = finalize_model_call_language_policy([*prepared.messages, {"role": "user", "content": TRIP_MESSAGE}])
    return prepared, messages


def test_pending_escalation_switches_tools_plan_prompt_and_records_the_event():
    source = _config(_candidate("direct"))
    prepared, messages = asyncio.run(_prepared(source))
    session = source.escalation_session
    session.rebuild_system_messages = prepared.rebuild_system_messages
    session.system_section_ids = frozenset(prepared.system_section_ids)
    state = AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-1", mode=source.plan_mode))
    state.step = 1
    emitter = _RecordingEmitter()
    runtime = _runtime(
        run_id="run-1",
        call_kwargs=source.call_kwargs,
        dynamic_tool_handlers=source.dynamic_tool_handlers,
        capability_resolution=source.capability_resolution,
        capability_escalation=session,
        escalation_tool_names=source.escalation_tool_names,
        skill_tool_names=source.skill_tool_names,
        network_budget=NetworkToolBudget(),
        emitter=emitter,
    )
    assert TOOL_USAGE_CONTRACT not in [message.section_id for message in messages]
    assert _request(source, package_id="fresh_web", reason="Needs current news.").status == "success"

    switched = asyncio.run(apply_pending_escalation(messages=messages, state=state, runtime=runtime))

    assert session.applied
    assert switched.capability_resolution.package_id == "fresh_web"
    assert switched.escalation_tool_names == frozenset()
    assert REQUEST_CAPABILITY_TOOL_NAME not in announced_tool_names_from_call_kwargs(switched.call_kwargs)
    assert "web_search" in announced_tool_names_from_call_kwargs(switched.call_kwargs)
    assert state.plan_coordinator.allowed_tool_names == frozenset(switched.capability_resolution.external_tool_names)
    section_ids = [message.section_id for message in messages if message.role == "system"]
    assert TOOL_USAGE_CONTRACT in section_ids
    assert section_ids.count(VISIBLE_RESPONSE_LANGUAGE) == 1
    assert section_ids[-1] == VISIBLE_RESPONSE_LANGUAGE
    assert messages[-1].role == "user"

    (event,) = emitter.escalations
    assert event["step_number"] == 1
    assert event["from_package_id"] == "direct"
    assert event["section_ids"] == section_ids
    resolution = TrajectoryCapabilityResolution.model_validate(event["capability_resolution"])
    assert resolution.package_id == "fresh_web"
    # 第二次调用不会重复切换。
    assert asyncio.run(apply_pending_escalation(messages=messages, state=state, runtime=switched)) is switched


def test_capability_escalated_event_survives_ledger_sanitizing():
    source = _config(_candidate("direct"))
    _request(source, package_id="weather", reason="x")
    from app.services.stream.agent_loop_lifecycle import capability_resolution_trajectory_payload

    payload = {
        "type": "capability_escalated",
        "protocol_version": 2,
        "run_id": "run-1",
        "sequence": 3,
        "trace_id": "run-1",
        "ts": 1.0,
        "step_number": 1,
        "from_package_id": "direct",
        "capability_resolution": capability_resolution_trajectory_payload(source.escalation_session.pending.config),
        "section_ids": ["app_identity", "current_date"],
        "system_prompt_fingerprint": "0" * 64,
    }

    event = ev.CapabilityEscalated.model_validate(payload)
    stored = build_trajectory_payload(event.model_dump())

    assert stored["capability_resolution"]["package_id"] == "weather"
    assert stored["from_package_id"] == "direct"
    assert stored["section_ids"] == ["app_identity", "current_date"]


def test_agent_loop_switches_before_the_round_after_the_request():
    from unittest.mock import patch

    from app.schemas.chat import Usage
    from app.services.stream.agent_loop_driver import run_agent_loop
    from app.services.stream.agent_loop_outcome import AgentLoopExit, AgentLoopOutcome
    from app.services.stream.agent_round import AgentRoundResult
    from app.services.stream.step_lifecycle import AgentStepContext

    source = _config(_candidate("direct"))
    prepared, messages = asyncio.run(_prepared(source))
    session = source.escalation_session
    session.rebuild_system_messages = prepared.rebuild_system_messages
    session.system_section_ids = frozenset(prepared.system_section_ids)
    seen_tools: list[list[str]] = []

    async def run_round(**kwargs):
        seen_tools.append(announced_tool_names_from_call_kwargs(kwargs["call_kwargs"]))
        if len(seen_tools) == 1:
            # 模拟本轮执行了 request_capability。
            await source.dynamic_tool_handlers[REQUEST_CAPABILITY_TOOL_NAME].execute(
                {"package_id": "fresh_web", "reason": "Needs current news."}
            )
        return AgentRoundResult(
            reasoning_buf="",
            content_buf="",
            tool_calls=[],
            finish_reason="stop",
            accumulated_usage=Usage(input_tokens=1, output_tokens=1),
        )

    outcomes = iter([None, AgentLoopOutcome(exit=AgentLoopExit.COMPLETED)])

    async def handle_outcome(*, request):
        return next(outcomes)

    async def start_step(**kwargs):
        number = kwargs["step_number"]
        return AgentStepContext(
            step_id=f"step-{number}",
            step_number=number,
            started_at=0.0,
            thinking_block_id=f"thinking-{number}",
            text_block_id=f"text-{number}",
        )

    emitter = _RecordingEmitter()
    runtime = _runtime(
        call_kwargs=source.call_kwargs,
        dynamic_tool_handlers=source.dynamic_tool_handlers,
        capability_resolution=source.capability_resolution,
        capability_escalation=session,
        escalation_tool_names=source.escalation_tool_names,
        network_budget=NetworkToolBudget(),
        emitter=emitter,
        run_round_fn=run_round,
        start_step_fn=start_step,
    )
    with patch("app.services.stream.agent_loop_driver.handle_agent_round_outcome", new=handle_outcome):
        outcome = asyncio.run(
            run_agent_loop(
                db=object(),
                messages=messages,
                state=AgentLoopState(plan_coordinator=PlanCoordinator(run_id="run-driver", mode=source.plan_mode)),
                runtime=runtime,
            )
        )

    assert outcome.exit == AgentLoopExit.COMPLETED
    assert REQUEST_CAPABILITY_TOOL_NAME in seen_tools[0]
    assert "web_search" not in seen_tools[0]
    assert "web_search" in seen_tools[1]
    assert REQUEST_CAPABILITY_TOOL_NAME not in seen_tools[1]
    assert [event["step_number"] for event in emitter.escalations] == [1]


def test_granted_result_spells_out_the_new_tools_parameters_from_the_sent_schemas():
    source = _config(_candidate("direct"))

    result = _request(
        source,
        package_id="mixed_itinerary",
        tool_names=["weather_forecast", "local_place_search"],
        primary_tool_name="weather_forecast",
        reason="x",
    )
    context = source.dynamic_tool_handlers[REQUEST_CAPABILITY_TOOL_NAME].format_llm_context(result)

    escalated = source.escalation_session.pending.config
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in escalated.call_kwargs["tools"]}
    for name in escalated.capability_resolution.external_tool_names:
        (line,) = [line for line in result.data["tool_signatures"] if line.startswith(f"{name}(")]
        assert f"- {line}" in context
        for prop in schemas[name]["properties"]:
            assert f"{prop}: " in line
        for prop in schemas[name].get("required", []):
            assert ", required" in line.split(f"{prop}: ", 1)[1].split(";", 1)[0]
    assert "location_source: one of named|current_location, required" in context


def test_granted_result_stored_before_signatures_still_renders():
    from app.services.tool_handlers.base import ToolResult

    source = _config(_candidate("direct"))
    handler = source.dynamic_tool_handlers[REQUEST_CAPABILITY_TOOL_NAME]
    legacy = ToolResult(
        status="success", data={"package_id": "weather", "tool_names": ["weather_forecast"], "reason": "x"}
    )

    assert "- weather_forecast" in handler.format_llm_context(legacy)
