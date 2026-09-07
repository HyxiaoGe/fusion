"""外部工具任务应保留通用联网替代工具。"""

import unittest

from app.schemas.trajectory import TrajectoryCapabilityResolution
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.run_capability_router import (
    _CandidateRoute,
    resolve_run_capability_route,
    serialize_capability_resolution,
)


class RecoveryRouteTests(unittest.TestCase):
    def test_external_routes_announce_optional_search_and_read(self):
        for package, primary, reason in [
            ("weather", ("weather_forecast",), "explicit_weather_request"),
            ("place_discovery", ("local_place_search",), "explicit_place_discovery"),
            ("mobility_route", ("route_compare",), "explicit_route_task"),
            ("flight", ("search_flights",), "explicit_flight_request"),
            ("train", ("search_trains",), "explicit_train_request"),
            ("travel_air_rail", ("search_flights", "search_trains"), "air_rail_comparison"),
            ("mixed_itinerary", ("weather_forecast", "route_compare", "search_trains"), "mixed_itinerary_request"),
            ("fresh_web", ("web_search",), "fresh_external_fact"),
            ("url_read", ("url_read",), "explicit_url_read"),
            ("mcp_explicit", ("mcp_authorized_tool",), "explicit_authorized_tool_alias"),
        ]:
            with self.subTest(package=package, primary=primary, reason=reason):
                candidate = _CandidateRoute(
                    package,
                    "high",
                    (reason,),
                    package not in {"place_discovery", "url_read", "mcp_explicit"},
                    explicit_tool_names=primary,
                )
                route = resolve_run_capability_route(
                    original_message="查询当前信息",
                    task_context_messages=None,
                    available_tool_names=[*primary, "web_search", "url_read"],
                    requested_plan_mode="off",
                    task_policy=AgentTaskPolicy("standard", "off", "standard", "standard"),
                    capabilities={"functionCalling": True, "searchCapable": True},
                    tools_disabled=False,
                    knowledge_grounded=False,
                    classify_fn=lambda **_: candidate,
                )
                assert set(route.external_tool_names) == {*primary, "web_search", "url_read"}
                TrajectoryCapabilityResolution.model_validate(
                    {**serialize_capability_resolution(route), "bundle_fingerprint": "sha256:" + "a" * 64}
                )

    def test_missing_optional_tools_does_not_disable_weather(self):
        route = resolve_run_capability_route(
            original_message="香港三天天气",
            task_context_messages=None,
            available_tool_names=["weather_forecast", "web_search", "url_read"],
            unavailable_tool_names=["web_search", "url_read"],
            requested_plan_mode="off",
            task_policy=AgentTaskPolicy("standard", "off", "standard", "standard"),
            capabilities={"functionCalling": True, "searchCapable": True},
            tools_disabled=False,
            knowledge_grounded=False,
        )
        assert route.package_id == "weather"
        assert route.external_tool_names == ("weather_forecast",)

    def test_weather_request_schemas_include_recovery_without_requiring_it(self):
        weather = next(tool for tool in AMAP_PRODUCT_DEFINITIONS if tool["function"]["name"] == "weather_forecast")
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": True},
            original_message="香港三天天气",
            additional_tools=[weather],
            dynamic_tool_handlers={"weather_forecast": lambda _: None},
        )
        assert set(config.announced_tools) == {"weather_forecast", "web_search", "url_read"}
        assert {tool["function"]["name"] for tool in config.call_kwargs["tools"]} == {
            "weather_forecast",
            "web_search",
            "url_read",
            "update_plan",
        }
        assert config.required_initial_tool_counts == {"weather_forecast": 1}

    def test_failed_primary_plan_can_add_search_and_read_recovery(self):
        for mode in ["auto", "on"]:
            with self.subTest(mode=mode):
                from app.services.agent.plan_coordinator import PlanCoordinator

                coordinator = PlanCoordinator(
                    run_id="recovery-plan",
                    mode=mode,
                    allowed_tool_names=frozenset({"route_compare", "web_search", "url_read"}),
                    required_initial_tool_counts={"route_compare": 1},
                )
                primary = {
                    "id": "primary",
                    "title": "查询路线",
                    "status": "pending",
                    "kind": "other",
                    "depends_on": [],
                    "planned_tools": ["route_compare"],
                }
                answer = {
                    "id": "answer",
                    "title": "整理答案",
                    "status": "pending",
                    "kind": "answer",
                    "depends_on": ["primary"],
                    "planned_tools": [],
                }
                assert coordinator.apply_model_update({"items": [primary, answer]}).accepted
                coordinator.mark_tools_started(["primary"])
                coordinator.mark_tool_results({"primary": "failed"})
                search = {
                    "id": "search",
                    "title": "搜索替代来源",
                    "status": "pending",
                    "kind": "other",
                    "depends_on": [],
                    "planned_tools": ["web_search"],
                }
                read = {
                    "id": "read",
                    "title": "读取来源",
                    "status": "pending",
                    "kind": "other",
                    "depends_on": ["search"],
                    "planned_tools": ["url_read"],
                }
                updated = coordinator.apply_model_update(
                    {
                        "items": [
                            {**primary, "status": "failed"},
                            search,
                            read,
                            {**answer, "depends_on": ["primary", "read"]},
                        ]
                    }
                )
                assert updated.accepted, updated.reason
                assert coordinator.plan_item_id_for_tool("web_search", requested_item_id="search") == "search"
                coordinator.mark_tools_started(["search"])
                coordinator.mark_tool_results({"search": "completed"})
                assert coordinator.plan_item_id_for_tool("url_read", requested_item_id="read") == "read"


class RecoveryPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_policy_reaches_weather_and_verified_model_context(self):
        from app.services.stream.agent_loop_request_prep import prepare_agent_loop_messages

        async def messages(*args, **kwargs):
            return [{"role": "user", "content": "查询"}]

        for message in ("香港三天天气", "核验 OpenAI 最新公告，给出官方原文和交叉来源"):
            config = build_agent_loop_call_config(
                provider="openai",
                options={"plan_mode": "off"},
                capabilities={"functionCalling": True, "searchCapable": True},
                original_message=message,
                additional_tools=AMAP_PRODUCT_DEFINITIONS,
                dynamic_tool_handlers={tool["function"]["name"]: lambda _: None for tool in AMAP_PRODUCT_DEFINITIONS},
            )
            prepared = await prepare_agent_loop_messages(
                db=object(),
                user_id="user-1",
                raw_messages=[],
                has_vision=False,
                file_ids=None,
                original_message=message,
                call_config=config,
                file_repo_factory=lambda _: object(),
                load_user_system_prompt_fn=lambda *_: None,
                build_llm_messages_fn=messages,
                preprocess_user_input=False,
            )
            assert "tool_failure_policy" in prepared.prompt_assembly["section_ids"]
            assert any(
                "use another announced tool" in item["content"].lower()
                for item in prepared.messages
                if item["role"] == "system"
            )
            if config.capability_resolution.package_id == "verified_web":
                assert "skill:verified-research@1.0.0" in prepared.prompt_assembly["section_ids"]
