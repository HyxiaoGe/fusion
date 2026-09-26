from __future__ import annotations

import pytest

from app.schemas.trajectory import TrajectoryCapabilityResolution
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.run_capability_router import (
    RunCapabilityResolution,
    _CandidateRoute,
    resolve_run_capability_route,
    serialize_capability_resolution,
)

ALL_TOOLS = [
    "mcp_unrelated_tool",
    "search_trains",
    "web_search",
    "route_compare",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "search_flights",
]


def _task_policy(*, task_mode: str = "standard", plan_mode: str = "auto") -> AgentTaskPolicy:
    if task_mode == "deep_research":
        return AgentTaskPolicy(
            task_mode="deep_research",
            plan_mode="on",
            network_profile="deep_research",
            evidence_policy="deep_research_v1",
        )
    return AgentTaskPolicy(
        task_mode="standard",
        plan_mode=plan_mode,
        network_profile="standard",
        evidence_policy="standard",
    )


def _resolve(
    message: str,
    *,
    task_context_messages: list[object] | None = None,
    available_tool_names: list[str] | None = None,
    requested_plan_mode: str = "auto",
    task_mode: str = "standard",
    capabilities: dict | None = None,
    tools_disabled: bool = False,
    knowledge_grounded: bool = False,
    load_skills_fn=None,
    classify_fn=None,
) -> RunCapabilityResolution:
    return resolve_run_capability_route(
        original_message=message,
        task_context_messages=task_context_messages,
        available_tool_names=available_tool_names or ALL_TOOLS,
        requested_plan_mode=requested_plan_mode,
        task_policy=_task_policy(task_mode=task_mode, plan_mode=requested_plan_mode),
        capabilities=capabilities or {"functionCalling": True, "searchCapable": True},
        tools_disabled=tools_disabled,
        knowledge_grounded=knowledge_grounded,
        load_skills_fn=load_skills_fn,
        classify_fn=classify_fn,
    )


def test_current_new_release_phrasing_degrades_when_tools_are_disabled():
    candidate = _CandidateRoute("fresh_web", "high", ("fresh_external_fact",), True)
    route = _resolve("今天 OpenAI 有什么新发布？", tools_disabled=True, classify_fn=lambda **_: candidate)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.network_boundary_required is True


def test_model_denials_remove_recovery_tools_without_dropping_requested_product_tool():
    candidate = _CandidateRoute(
        "weather",
        "high",
        ("explicit_weather_request",),
        True,
        network_policy="no_web_search",
        denied_tool_names=("url_read",),
    )
    route = _resolve("查上海天气，但别搜索也别打开网页", classify_fn=lambda **_: candidate)

    assert route.package_id == "weather"
    assert route.external_tool_names == ("weather_forecast",)
    assert route.denied_product_tool_names == frozenset({"web_search", "url_read", "mcp_unrelated_tool"})
    snapshot = serialize_capability_resolution(route)
    assert snapshot["denied_product_tool_names"] == ["mcp_unrelated_tool", "url_read", "web_search"]
    TrajectoryCapabilityResolution.model_validate({**snapshot, "bundle_fingerprint": "sha256:" + "0" * 64})


def test_model_denial_of_required_tool_closes_tool_path():
    candidate = _CandidateRoute(
        "train",
        "high",
        ("explicit_train_request",),
        True,
        denied_tool_names=("search_trains",),
    )
    route = _resolve("查北京到上海高铁，但不要调用 search_trains", classify_fn=lambda **_: candidate)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.network_boundary_required is True


def test_whole_request_network_denial_blocks_product_and_recovery_tools():
    candidate = _CandidateRoute(
        "weather",
        "high",
        ("explicit_weather_request",),
        True,
        network_policy="no_network",
    )
    route = _resolve("本次不要联网，查明天上海天气", classify_fn=lambda **_: candidate)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.network_boundary_required is True
    assert {"web_search", "url_read", "weather_forecast", "mcp_unrelated_tool"}.issubset(
        route.denied_product_tool_names
    )


@pytest.mark.parametrize(
    ("package_id", "reason_code", "denied_tool_name"),
    [
        ("verified_web", "verified_source_request", "web_search"),
        ("verified_web", "verified_source_request", "url_read"),
        ("travel_air_rail", "air_rail_comparison", "search_flights"),
        ("travel_air_rail", "air_rail_comparison", "search_trains"),
    ],
)
def test_comparison_and_verification_packages_degrade_when_one_required_tool_is_denied(
    package_id: str, reason_code: str, denied_tool_name: str
) -> None:
    candidate = _CandidateRoute(package_id, "high", (reason_code,), True, denied_tool_names=(denied_tool_name,))

    route = _resolve("执行任务，但不要使用其中一个必需工具", classify_fn=lambda **_: candidate)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.effective_plan_mode == "off"
    assert route.network_boundary_required is True
    assert route.reason_codes == ("required_tools_unavailable",)


@pytest.mark.parametrize("package_id", ["mobility_intercity", "mixed_itinerary"])
def test_multi_product_route_freezes_one_primary_tool(package_id: str) -> None:
    reason_code = "intercity_locations" if package_id == "mobility_intercity" else "mixed_itinerary_request"
    explicit_tool_names = ("route_compare", "search_flights") if package_id == "mixed_itinerary" else None
    candidate = _CandidateRoute(
        package_id,
        "medium" if package_id == "mobility_intercity" else "high",
        ("origin_destination_relation", reason_code) if package_id == "mobility_intercity" else (reason_code,),
        True,
        explicit_tool_names=explicit_tool_names,
        required_primary_tool_name="route_compare",
    )

    route = _resolve("规划跨城出行", classify_fn=lambda **_: candidate)

    assert route.package_id == package_id
    assert route.required_primary_tool_name == "route_compare"
    assert route.effective_plan_mode == "auto"
    snapshot = serialize_capability_resolution(route)
    assert snapshot["required_primary_tool_name"] == "route_compare"
    assert snapshot["denied_product_tool_names"] == []


def test_multi_product_route_without_usable_primary_tool_degrades() -> None:
    candidate = _CandidateRoute(
        "mobility_intercity",
        "medium",
        ("origin_destination_relation", "intercity_locations"),
        True,
        required_primary_tool_name="route_compare",
        denied_tool_names=("route_compare",),
    )

    route = _resolve("规划跨城出行", classify_fn=lambda **_: candidate)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.network_boundary_required is True


@pytest.mark.parametrize(
    ("message", "kwargs", "expected_reason"),
    [
        (
            "查一下今天最新的 OpenAI 新闻",
            {"tools_disabled": True},
            "tools_disabled",
        ),
        (
            "查今天上海天气",
            {"capabilities": {"functionCalling": False, "searchCapable": True}},
            "function_calling_unavailable",
        ),
        (
            "查一下今天最新的 OpenAI 新闻",
            {"capabilities": {"functionCalling": True, "searchCapable": False}},
            "search_capability_unavailable",
        ),
    ],
)
def test_tool_degradation_uses_network_boundary(message, kwargs, expected_reason):
    candidate = _CandidateRoute("fresh_web", "high", ("fresh_external_fact",), True)
    route = _resolve(message, classify_fn=lambda **_: candidate, **kwargs)

    assert route.package_id == "tools_unavailable"
    assert route.external_tool_names == ()
    assert route.effective_plan_mode == "off"
    assert route.include_current_date is True
    assert route.network_boundary_required is True
    assert route.resolution_mode == "degraded"
    assert route.reason_codes == (expected_reason,)


def test_knowledge_grounded_marks_blocked_fresh_request_with_date_and_boundary():
    candidate = _CandidateRoute("fresh_web", "high", ("fresh_external_fact",), True)
    route = _resolve(
        "查一下最新的 OpenAI 新闻",
        knowledge_grounded=True,
        tools_disabled=True,
        requested_plan_mode="on",
        classify_fn=lambda **_: candidate,
    )

    assert route.package_id == "knowledge_grounded"
    assert route.external_tool_names == ()
    assert route.effective_plan_mode == "off"
    assert route.include_current_date is True
    assert route.network_boundary_required is True
    assert route.reason_codes == ("knowledge_grounded_mode",)


def test_explicit_plan_mode_overrides_package_auto_policy():
    candidate = _CandidateRoute("direct", "high", ("stable_knowledge_question",), False)
    forced_on = _resolve("你好", requested_plan_mode="on", classify_fn=lambda **_: candidate)
    forced_off = _resolve(
        "从上海虹桥站到外滩怎么坐公共交通？",
        requested_plan_mode="off",
        classify_fn=lambda **_: _CandidateRoute("mobility_route", "high", ("explicit_route_task",), True),
    )

    assert forced_on.package_id == "direct"
    assert forced_on.effective_plan_mode == "on"
    assert forced_off.package_id == "mobility_route"
    assert forced_off.effective_plan_mode == "off"


def test_topic_switch_does_not_inherit_old_route_capability():
    candidate = _CandidateRoute("transform", "high", ("text_transform_request",), False)
    route = _resolve(
        "把 See you tomorrow 翻译成中文",
        task_context_messages=[
            {"role": "user", "content": "从北京到上海怎么走？"},
            {"role": "assistant", "content": [{"type": "route_results", "schema_version": 1}]},
            {"role": "user", "content": "讲讲 Python 3.14"},
            {"role": "assistant", "content": [{"type": "text", "text": "Python 3.14 的变化如下。"}]},
            {"role": "user", "content": "把 See you tomorrow 翻译成中文"},
        ],
        classify_fn=lambda **_: candidate,
    )

    assert route.package_id == "transform"
    assert route.external_tool_names == ()


def test_capability_classifier_is_replaceable_without_touching_the_skeleton():
    """issue #24：换掉"消息 → 能力包"这一步，resolution 骨架完全不变。"""

    calls: list[dict] = []

    def stub_classifier(*, message, task_context_messages, available_tool_names):
        calls.append(
            {
                "message": message,
                "task_context_messages": task_context_messages,
                "available_tool_names": available_tool_names,
            }
        )
        return _CandidateRoute(
            "weather",
            "high",
            ("explicit_weather_request",),
            True,
        )

    resolution = resolve_run_capability_route(
        original_message="随便说点什么",
        task_context_messages=None,
        available_tool_names=ALL_TOOLS,
        requested_plan_mode="auto",
        task_policy=_task_policy(task_mode="standard", plan_mode="auto"),
        capabilities={"functionCalling": True, "searchCapable": True},
        tools_disabled=False,
        knowledge_grounded=False,
        classify_fn=stub_classifier,
    )

    assert len(calls) == 1
    assert calls[0]["message"] == "随便说点什么"
    # 契约校验、工具派生、指纹与 Skill 终态仍由骨架完成。
    assert resolution.package_id == "weather"
    assert resolution.external_tool_names == ("web_search", "url_read", "weather_forecast")
    assert resolution.resolution_mode == "routed"
    assert resolution.include_current_date is True
    assert resolution.skill_resolution is not None


def test_replaced_classifier_still_goes_through_contract_validation():
    """替换分类器不等于绕过契约：非法包与工具组合仍必须被拒。"""

    def invalid_classifier(*, message, task_context_messages, available_tool_names):
        return _CandidateRoute(
            "direct",
            "high",
            ("direct_greeting",),
            False,
            explicit_tool_names=("web_search",),
        )

    with pytest.raises(ValueError):
        resolve_run_capability_route(
            original_message="你好",
            task_context_messages=None,
            available_tool_names=ALL_TOOLS,
            requested_plan_mode="auto",
            task_policy=_task_policy(task_mode="standard", plan_mode="auto"),
            capabilities={"functionCalling": True, "searchCapable": True},
            tools_disabled=False,
            knowledge_grounded=False,
            classify_fn=invalid_classifier,
        )
