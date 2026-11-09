import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import PlaceResultsBlock, RouteResultsBlock
from app.services.external.agent_tools_mcp import (
    GEOLOCATION_META_KEY,
    GatewayToolCatalog,
    GatewayToolOutcome,
    GatewayToolSpec,
)
from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.maps_quota import hide_seconds
from app.services.mcp.maps_tools import (
    LOCAL_PLACE_SEARCH,
    ROUTE_COMPARE,
    MapsRunControls,
    MapsToolHandler,
    build_maps_binding,
    build_maps_definition,
)
from app.services.tool_handlers.base import ToolRuntimeContext
from app.utils.time import CHINA_TZ
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository

_CANDIDATE = {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 5}, {"type": "null"}], "default": None}
_OPTIONAL_TEXT = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}
# 与 dev 上 /maps/mcp 的 tools/list 同形（说明取开头一句）。
PLACE_TOOL = {
    "name": "place_search",
    "description": "Search for places such as restaurants, shops, attractions and facilities in mainland China.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "anchor_source": {"type": "string", "enum": ["named", "current_location", "none"]},
            "city": _OPTIONAL_TEXT,
            "near": _OPTIONAL_TEXT,
            "near_candidate": _CANDIDATE,
            "radius_m": {"type": "integer", "minimum": 100, "maximum": 50000, "default": 3000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
        },
        "required": ["query", "anchor_source"],
    },
}
ROUTE_TOOL = {
    "name": "route_compare",
    "description": "Compare up to three transport modes between two places in mainland China.",
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "origin_source": {"type": "string", "enum": ["named", "current_location"]},
            "destination_source": {"type": "string", "enum": ["named", "current_location"]},
            "modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["driving", "transit", "walking", "bicycling"]},
                "minItems": 1,
                "maxItems": 3,
            },
            "origin_city": _OPTIONAL_TEXT,
            "destination_city": _OPTIONAL_TEXT,
            "origin_candidate": _CANDIDATE,
            "destination_candidate": _CANDIDATE,
            "origin_place_id": _OPTIONAL_TEXT,
            "destination_place_id": _OPTIONAL_TEXT,
            "requested_departure_time": _OPTIONAL_TEXT,
        },
        "required": ["origin", "destination", "origin_source", "destination_source", "modes"],
    },
}
PLACE_SPEC = GatewayToolSpec(service="maps", **PLACE_TOOL)
ROUTE_SPEC = GatewayToolSpec(service="maps", **ROUTE_TOOL)
MAPS_DEFINITIONS = [
    build_maps_definition(PLACE_SPEC, name=LOCAL_PLACE_SEARCH),
    build_maps_definition(ROUTE_SPEC, name=ROUTE_COMPARE),
]
PLACE_ARGS = {"query": "火锅", "anchor_source": "named", "near": "静安寺", "city": "上海"}
ROUTE_ARGS = {
    "origin": "上海虹桥站",
    "destination": "外滩",
    "origin_source": "named",
    "destination_source": "named",
    "modes": ["transit", "driving"],
}
MAP_URL = "https://uri.amap.com/marker?poiid=B0FFG1&src=fusion&callnative=0"


def amap_place(**overrides):
    place = {
        "place_id": "amap_B0FFG1",
        "name": "某某火锅",
        "address": "南京西路 1 号",
        "district": "静安区",
        "city": "上海市",
        "category": "餐饮服务;中餐厅;火锅店",
        "distance_m": 320,
        "rating": 4.6,
        "reference_cost_yuan": 128,
        "open_hours": "10:00-22:00",
        "business_area": "静安寺",
        "photo_url": "https://aos-comment.amap.com/photo.jpg",
        "map_url": MAP_URL,
        "provider": "amap",
    }
    place.update(overrides)
    return {key: value for key, value in place.items() if value is not None}


def place_outcome(places, *, providers=("amap",), limitations=(), anchor=None):
    result = {
        "ok": True,
        "status": "success",
        "query": "火锅",
        "city": "上海",
        "near": "静安寺",
        "anchor": anchor or {"source": "named", "label": "静安寺", "city": "上海市"},
        "result_count": len(places),
        "places": places,
        "limitations": list(limitations),
        "providers": list(providers),
    }
    model_view = {key: value for key, value in result.items() if key not in {"ok", "providers"}}
    return GatewayToolOutcome(ok=True, data=result, model_text=json.dumps(model_view, ensure_ascii=False))


def route_outcome(routes, *, status="success", unavailable_modes=(), limitations=(), providers=("amap",)):
    result = {
        "ok": True,
        "status": status,
        "origin": {"source": "named", "label": "上海虹桥站", "city": "上海市"},
        "destination": {"source": "named", "label": "外滩", "city": "上海市"},
        "routes": routes,
        "unavailable_modes": list(unavailable_modes),
        "limitations": list(limitations),
        "providers": list(providers),
    }
    return GatewayToolOutcome(ok=True, data=result, model_text=json.dumps({"routes": routes}, ensure_ascii=False))


TRANSIT_ROUTE = {
    "mode": "transit",
    "duration_s": 2700,
    "transit_type": "subway",
    "transfers": 1,
    "walking_distance_m": 600,
    "fare_yuan": 5,
    "legs": [
        {
            "kind": "subway",
            "line_name": "地铁2号线",
            "departure_stop": "虹桥火车站",
            "arrival_stop": "南京东路",
            "via_stop_count": 12,
        },
        {"kind": "walking", "distance_m": 600, "duration_s": 480},
    ],
    "alternatives": [
        {"transit_type": "bus", "duration_s": 3900, "transfers": 0, "legs": [{"kind": "bus", "line_name": "71路"}]}
    ],
    "provider": "amap",
}
DRIVING_ROUTE = {
    "mode": "driving",
    "distance_m": 21000,
    "duration_s": 2100,
    "summary": "速度最快",
    "toll_yuan": 0,
    "provider": "amap",
}


def failure(code, text, **error):
    return GatewayToolOutcome(
        ok=False,
        model_text=text,
        error_code=code,
        error={"code": code, "message": "failed", "retryable": False, **error},
    )


def maps_handler(tool_name, outcome=None, *, controls=None, call_tool=None, hide_tools_until=None):
    definition = MAPS_DEFINITIONS[0] if tool_name == LOCAL_PLACE_SEARCH else MAPS_DEFINITIONS[1]
    return MapsToolHandler(
        binding=build_maps_binding(definition),
        parameters=definition["function"]["parameters"],
        controls=controls or MapsRunControls(),
        call_tool=call_tool or AsyncMock(return_value=outcome),
        hide_tools_until=hide_tools_until or AsyncMock(),
    )


def loaded_catalog(tools):
    async def list_tools(service):
        return tools if service == "maps" else []

    catalog = GatewayToolCatalog(services=("maps",), list_tools=list_tools)
    asyncio.run(catalog.refresh())
    return catalog


class MapsDefinitionTests(unittest.TestCase):
    def test_place_search_is_announced_as_local_place_search_with_service_text_and_notes(self):
        definition = MAPS_DEFINITIONS[0]["function"]

        self.assertEqual(definition["name"], LOCAL_PLACE_SEARCH)
        self.assertTrue(definition["description"].startswith(PLACE_TOOL["description"] + "\n\nIn this application:"))
        self.assertIn("place cards", definition["description"])
        self.assertEqual(definition["parameters"]["required"], ["query", "anchor_source"])
        self.assertEqual(build_maps_binding(MAPS_DEFINITIONS[0]).remote_tool_name, "place_search")

    def test_route_notes_keep_the_combined_itinerary_transfer_rule(self):
        description = MAPS_DEFINITIONS[1]["function"]["description"]

        self.assertTrue(description.startswith(ROUTE_TOOL["description"]))
        self.assertIn("station_name as origin", description)
        self.assertIn("route cards", description)


class MapsRegistrationTests(unittest.TestCase):
    def load(self, catalog, *, hidden=False):
        return load_mcp_agent_tools(
            object(),
            user_id="user-123",
            client_manager=FakeClientManager(),
            session_factory=FakeDb,
            repository_factory=lambda _db: FakeRepository([]),
            gateway_catalog=catalog,
            maps_hidden=lambda: hidden,
        )

    def test_tools_come_from_the_service_catalog_and_share_one_run_budget(self):
        tools = self.load(loaded_catalog([PLACE_TOOL, ROUTE_TOOL]))

        self.assertEqual(
            [definition["function"]["name"] for definition in tools.definitions],
            [LOCAL_PLACE_SEARCH, ROUTE_COMPARE],
        )
        self.assertEqual(
            [(binding["alias"], binding["remote_tool_name"]) for binding in tools.audit_bindings],
            [(LOCAL_PLACE_SEARCH, "place_search"), (ROUTE_COMPARE, "route_compare")],
        )
        self.assertIs(tools.handlers[LOCAL_PLACE_SEARCH].controls, tools.handlers[ROUTE_COMPARE].controls)

    def test_missing_catalog_entry_or_exhausted_quota_does_not_announce_the_tools(self):
        self.assertEqual(
            [definition["function"]["name"] for definition in self.load(loaded_catalog([ROUTE_TOOL])).definitions],
            [ROUTE_COMPARE],
        )
        self.assertEqual(self.load(loaded_catalog([PLACE_TOOL, ROUTE_TOOL]), hidden=True).definitions, [])


class MapsExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_place_search_passes_arguments_and_builds_place_card(self):
        call_tool = AsyncMock(return_value=place_outcome([amap_place()]))
        handler = maps_handler(LOCAL_PLACE_SEARCH, call_tool=call_tool)

        result = await handler.execute(PLACE_ARGS)
        block = handler.build_content_block(result, "blk_1", "log_1")

        call_tool.assert_awaited_once()
        self.assertEqual(call_tool.await_args.args, ("maps", "place_search", PLACE_ARGS))
        self.assertEqual(call_tool.await_args.kwargs["scene"], LOCAL_PLACE_SEARCH)
        self.assertIsNone(call_tool.await_args.kwargs["meta"])
        self.assertIsInstance(block, PlaceResultsBlock)
        self.assertEqual((block.provider, block.attribution.label, block.near), ("amap", "高德地图", "静安寺"))
        place = block.places[0]
        self.assertEqual(place.provider_place_id, "amap_B0FFG1")
        self.assertEqual(place.platform_url, MAP_URL)
        self.assertEqual([(action.label, action.url) for action in place.actions], [("查看详情", MAP_URL)])
        self.assertEqual(place.photos[0].url, "https://aos-comment.amap.com/photo.jpg")
        self.assertEqual(block.limitations, ["不包含实时排队或空位信息"])

    async def test_tencent_places_have_no_platform_link_and_limitations_are_shown_in_chinese(self):
        tmap_place = amap_place(place_id="tmap_123", map_url=None, photo_url=None, provider="tmap")
        handler = maps_handler(
            LOCAL_PLACE_SEARCH,
            place_outcome([tmap_place], providers=("amap", "tmap"), limitations=["first_term_only", "unknown_code"]),
        )

        block = handler.build_content_block(await handler.execute(PLACE_ARGS), "blk_1", "log_1")

        self.assertEqual((block.provider, block.attribution.label), ("amap,tmap", "高德地图、腾讯地图"))
        self.assertEqual(
            (block.places[0].platform_url, block.places[0].actions, block.places[0].photos), (None, [], [])
        )
        self.assertEqual(block.limitations, ["不包含实时排队或空位信息", "本次只按第一个关键词搜索，其他关键词未覆盖"])

    async def test_overlong_service_text_is_trimmed_instead_of_dropping_the_card(self):
        handler = maps_handler(
            LOCAL_PLACE_SEARCH,
            place_outcome(
                [amap_place(name="店" * 200, address="路" * 300, rating=9, distance_m=-1), {"place_id": "x"}]
            ),
        )

        block = handler.build_content_block(await handler.execute(PLACE_ARGS), "blk_1", "log_1")

        self.assertEqual(block.result_count, 1)
        self.assertEqual((len(block.places[0].name), len(block.places[0].address)), (120, 240))
        self.assertEqual((block.places[0].rating, block.places[0].distance_m), (None, None))

    async def test_current_location_sends_device_location_in_meta(self):
        call_tool = AsyncMock(return_value=place_outcome([amap_place()], anchor={"source": "current_location"}))
        handler = maps_handler(LOCAL_PLACE_SEARCH, call_tool=call_tool)
        context = ToolRuntimeContext(geolocation=SimpleNamespace(latitude=31.2, longitude=121.4, accuracy_m=30))

        result = await handler.execute({"query": "咖啡", "anchor_source": "current_location"}, context)
        block = handler.build_content_block(result, "blk_1", "log_1")

        self.assertEqual(
            call_tool.await_args.kwargs["meta"],
            {GEOLOCATION_META_KEY: {"longitude": 121.4, "latitude": 31.2, "accuracy_m": 30}},
        )
        self.assertEqual(block.near, "当前位置")

    async def test_current_location_without_device_location_lets_the_service_answer(self):
        call_tool = AsyncMock(return_value=failure("location_unavailable", "Ask the user where they are."))
        handler = maps_handler(ROUTE_COMPARE, call_tool=call_tool)

        result = await handler.execute(
            {**ROUTE_ARGS, "origin": "current_location", "origin_source": "current_location"}
        )

        self.assertIsNone(call_tool.await_args.kwargs["meta"])
        self.assertEqual((result.status, result.data["error_code"]), ("failed", "location_unavailable"))
        self.assertEqual(handler.format_llm_context(result), "Ask the user where they are.")

    async def test_route_card_keeps_transit_legs_and_degraded_status(self):
        handler = maps_handler(
            ROUTE_COMPARE,
            route_outcome(
                [TRANSIT_ROUTE, DRIVING_ROUTE],
                status="degraded",
                unavailable_modes=["walking"],
                limitations=["departure_time_not_applied"],
            ),
        )

        result = await handler.execute(ROUTE_ARGS)
        block = handler.build_content_block(result, "blk_1", "log_1")

        self.assertEqual(result.status, "degraded")
        self.assertIsInstance(block, RouteResultsBlock)
        self.assertEqual((block.origin.label, block.destination.city), ("上海虹桥站", "上海市"))
        transit, driving = block.routes
        self.assertEqual((transit.mode, transit.transfers, transit.distance_m), ("transit", 1, None))
        self.assertEqual([leg.line_name for leg in transit.legs], ["地铁2号线", None])
        self.assertEqual(transit.alternatives[0].legs[0].line_name, "71路")
        self.assertEqual((driving.distance_m, driving.summary, driving.legs), (21000, "速度最快", []))
        self.assertEqual(block.unavailable_modes, ["walking"])
        self.assertEqual(
            block.limitations, ["路线时间和距离仅代表本次查询结果", "路线按查询时刻计算，未按指定的出发时间"]
        )

    async def test_success_context_wraps_service_text_as_untrusted_data(self):
        handler = maps_handler(LOCAL_PLACE_SEARCH, place_outcome([amap_place(name="<b>店</b>")]))

        context = handler.format_llm_context(await handler.execute(PLACE_ARGS))

        self.assertIn('<amap_product_result tool="local_place_search">', context)
        self.assertIn("&lt;b&gt;店&lt;/b&gt;", context)
        self.assertNotIn(MAP_URL, context)

    async def test_ambiguous_location_keeps_candidates_for_place_choice(self):
        candidates = [{"candidate": 1, "city": "上海市", "district": "静安区", "level": "兴趣点"}]
        handler = maps_handler(
            ROUTE_COMPARE,
            failure(
                "ambiguous_location",
                "The place name matches several locations.",
                place_name="人民广场",
                candidate_argument="destination_candidate",
                candidates=candidates,
            ),
        )

        result = await handler.execute(ROUTE_ARGS)

        self.assertEqual(result.data["error_code"], "ambiguous_location")
        self.assertEqual(
            result.data["error_details"],
            {"place_name": "人民广场", "candidate_argument": "destination_candidate", "candidates": candidates},
        )
        self.assertEqual(handler.sanitize_output_data_for_log(result)["candidate_count"], 1)
        self.assertIsNone(handler.build_content_block(result, "blk_1", "log_1"))

    async def test_gateway_error_without_service_text_uses_fusion_fallback(self):
        handler = maps_handler(LOCAL_PLACE_SEARCH, GatewayToolOutcome(ok=False, error_code="rate_limited"))

        result = await handler.execute(PLACE_ARGS)

        self.assertEqual(result.data["error_code"], "rate_limited")
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("maps.unavailable"))

    async def test_empty_service_text_is_an_invalid_response(self):
        handler = maps_handler(LOCAL_PLACE_SEARCH, GatewayToolOutcome(ok=True, data={"ok": True}, model_text=""))

        result = await handler.execute(PLACE_ARGS)

        self.assertEqual((result.status, result.data["error_code"]), ("failed", "invalid_response"))

    async def test_quota_exhausted_hides_the_tools_until_reset(self):
        hide = AsyncMock()
        handler = maps_handler(
            ROUTE_COMPARE,
            failure("quota_exhausted", "Map quota is used up.", reset_at="2026-11-10T00:00:00+08:00"),
            hide_tools_until=hide,
        )

        await handler.execute(ROUTE_ARGS)

        hide.assert_awaited_once_with("2026-11-10T00:00:00+08:00")

    async def test_run_budget_is_shared_and_checked_before_calling(self):
        controls = MapsRunControls(max_calls=1)
        call_tool = AsyncMock(return_value=place_outcome([amap_place()]))
        place = maps_handler(LOCAL_PLACE_SEARCH, controls=controls, call_tool=call_tool)
        route = maps_handler(ROUTE_COMPARE, controls=controls, call_tool=call_tool)

        await place.execute(PLACE_ARGS)
        result = await route.execute(ROUTE_ARGS)

        self.assertEqual(call_tool.await_count, 1)
        self.assertEqual(result.data["error_code"], "server_run_budget_exhausted")
        self.assertTrue(await route.is_run_budget_exhausted())


class MapsArgumentTests(unittest.TestCase):
    def test_schema_validation_uses_the_announced_parameters(self):
        handler = maps_handler(ROUTE_COMPARE)

        self.assertEqual(handler.validate_arguments(ROUTE_ARGS), [])
        self.assertTrue(handler.validate_arguments({**ROUTE_ARGS, "modes": []}))

    def test_station_city_from_this_runs_train_card_is_required_or_must_match(self):
        handler = maps_handler(ROUTE_COMPARE)
        context = ToolRuntimeContext(route_city_hints={"上海虹桥站": ("上海",)})

        self.assertEqual(
            handler.check_runtime_arguments(ROUTE_ARGS, context),
            {
                "error_code": "route_city_required",
                "validation_errors": ["origin_city:required"],
                "hints": {"origin_city": ["上海"]},
            },
        )
        self.assertEqual(
            handler.check_runtime_arguments({**ROUTE_ARGS, "origin_city": "苏州"}, context)["error_code"],
            "route_city_mismatch",
        )
        self.assertIsNone(handler.check_runtime_arguments({**ROUTE_ARGS, "origin_city": "上海市"}, context))
        self.assertIsNone(handler.check_runtime_arguments({**ROUTE_ARGS, "origin_place_id": "amap_B0FFG1"}, context))
        self.assertIsNone(maps_handler(LOCAL_PLACE_SEARCH).check_runtime_arguments(PLACE_ARGS, context))

    def test_call_signature_ignores_whitespace_and_null_optionals(self):
        handler = maps_handler(LOCAL_PLACE_SEARCH)

        self.assertEqual(
            handler.build_successful_call_signature(PLACE_ARGS),
            handler.build_successful_call_signature({**PLACE_ARGS, "near": " 静安寺 ", "near_candidate": None}),
        )
        self.assertNotEqual(
            handler.build_successful_call_signature(PLACE_ARGS),
            maps_handler(ROUTE_COMPARE).build_successful_call_signature(PLACE_ARGS),
        )


class MapsQuotaTests(unittest.TestCase):
    def test_hide_seconds_follows_reset_at_within_bounds(self):
        now = datetime(2026, 11, 9, 20, 0, tzinfo=CHINA_TZ)

        self.assertEqual(hide_seconds("2026-11-10T00:00:00+08:00", now=now), 4 * 3600)
        self.assertEqual(hide_seconds("2026-11-09T19:00:00+08:00", now=now), 60)
        self.assertEqual(hide_seconds("not a time", now=now), 32 * 24 * 3600)
        self.assertEqual(hide_seconds(None, now=now), 32 * 24 * 3600)

    def test_hide_and_read_share_one_redis_key(self):
        from app.services.mcp import maps_quota

        store = {}

        class AsyncRedis:
            async def set(self, key, value, ex):
                store[key] = (value, ex)

        class SyncRedis:
            def get(self, key):
                return store.get(key, (None,))[0]

        with patch.object(maps_quota, "_get_sync_client", return_value=SyncRedis()):
            self.assertFalse(maps_quota.maps_tools_hidden())
            asyncio.run(maps_quota.hide_maps_tools_until("2099-01-01T00:00:00+08:00", redis_client=AsyncRedis()))
            self.assertTrue(maps_quota.maps_tools_hidden())
        self.assertEqual(list(store.values())[0][1], 32 * 24 * 3600)

    def test_redis_failure_keeps_the_tools_announced(self):
        from app.services.mcp import maps_quota

        class Broken:
            def get(self, key):
                raise ConnectionError

        with patch.object(maps_quota, "_get_sync_client", return_value=Broken()):
            self.assertFalse(maps_quota.maps_tools_hidden())


if __name__ == "__main__":
    unittest.main()
