import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.db.models import Message as MessageModel
from app.db.repositories import ConversationRepository
from app.schemas.chat import (
    PlacePhoto,
    PlaceResult,
    PlaceResultsBlock,
    RouteEndpoint,
    RouteOption,
    RouteResultsBlock,
    StructuredResultAction,
    StructuredResultAttribution,
    TransitAlternative,
    TransitLeg,
    WeatherForecastDay,
    WeatherResultsBlock,
)
from app.services.admin_audit_service import AdminAuditService
from app.services.agent.continuation import deserialize_content_blocks
from app.services.agent.emitter import AgentEventEmitter
from app.services.stream.persistence import merge_partial_content_blocks


def place_block(*, block_id: str = "blk-place", result_count: int = 1) -> PlaceResultsBlock:
    places = [
        PlaceResult(
            provider_place_id="B0FFTEST",
            name="民治烤肉店",
            address="深圳市龙华区民治街道",
            district="龙华区",
            category="餐饮服务",
            distance_m=320,
            photos=[PlacePhoto(url="https://store.is.autonavi.com/photo.jpg", title="门店")],
            rating=4.6,
            reference_cost_yuan=98,
            business_area="民治",
            open_hours="周一至周日 11:00-23:00",
            detail_status="enriched",
            platform_url="https://uri.amap.com/marker?poiid=B0FFTEST&src=fusion&callnative=0",
        )
    ][:result_count]
    return PlaceResultsBlock(
        type="place_results",
        id=block_id,
        schema_version=1,
        provider="amap",
        attribution=StructuredResultAttribution(label="高德地图"),
        query="烤肉",
        near="民治地铁站",
        status="success",
        result_count=len(places),
        places=places,
        limitations=["不包含实时排队或空位信息", "参考消费不代表人均或实时价格"],
        tool_call_log_id="log-place",
    )


def route_block(*, block_id: str = "blk-route") -> RouteResultsBlock:
    return RouteResultsBlock(
        type="route_results",
        id=block_id,
        schema_version=1,
        provider="amap",
        attribution=StructuredResultAttribution(label="高德地图"),
        status="degraded",
        origin=RouteEndpoint(label="民治地铁站", city="深圳市"),
        destination=RouteEndpoint(label="深圳市民中心", city="深圳市"),
        routes=[
            RouteOption(
                mode="driving",
                distance_m=16_000,
                duration_s=1_500,
                summary="推荐路线",
                toll_yuan=0,
            )
        ],
        unavailable_modes=["transit"],
        limitations=["路线时间和距离仅代表高德本次返回结果"],
        tool_call_log_id="log-route",
    )


class ProductResultSchemaTests(unittest.TestCase):
    def test_weather_block_enforces_safe_v1_contract(self):
        block = WeatherResultsBlock(
            type="weather_results",
            id="blk-weather",
            schema_version=1,
            provider="amap",
            attribution=StructuredResultAttribution(label="高德地图"),
            status="degraded",
            query="深圳市南山区",
            resolved_location="深圳市南山区",
            day_count=2,
            forecast_days=[
                WeatherForecastDay(
                    date=date(2026, 7, 23),
                    weekday=4,
                    day_weather="多云",
                    night_weather="阵雨",
                    high_c=32,
                    low_c=27,
                    day_wind_direction="南",
                    day_wind_power="≤3",
                ),
                WeatherForecastDay(
                    date=date(2026, 7, 24),
                    weekday=5,
                    day_weather="阵雨",
                    night_weather="多云",
                    high_c=31,
                    low_c=26,
                ),
            ],
            fetched_at=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
            limitations=["天气预报按行政区提供，不代表具体建筑物"],
            tool_call_log_id="log-weather",
        )

        payload = block.model_dump(mode="json")
        self.assertEqual(payload["forecast_days"][0]["date"], "2026-07-23")
        self.assertNotIn("coordinates", payload)
        self.assertNotIn("provider_payload", payload)

        invalid_payloads = [
            {**payload, "day_count": 1},
            {**payload, "fetched_at": "2026-07-23T08:00:00"},
            {
                **payload,
                "forecast_days": [payload["forecast_days"][1], payload["forecast_days"][0]],
            },
            {
                **payload,
                "forecast_days": [
                    {**payload["forecast_days"][0], "weekday": 5},
                    payload["forecast_days"][1],
                ],
            },
            {
                **payload,
                "forecast_days": [
                    {**payload["forecast_days"][0], "high_c": 101},
                    payload["forecast_days"][1],
                ],
            },
            {
                **payload,
                "forecast_days": [
                    {**payload["forecast_days"][0], "high_c": 20, "low_c": 27},
                    payload["forecast_days"][1],
                ],
            },
            {**payload, "provider": "future-provider"},
            {**payload, "coordinates": "114.1,22.5"},
        ]
        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    WeatherResultsBlock.model_validate(invalid)

        # 只给所问那一天时是完整结果；单日但不是所问日期仍算降级
        single = {**payload, "day_count": 1, "forecast_days": [payload["forecast_days"][1]]}
        self.assertEqual(
            WeatherResultsBlock.model_validate({**single, "status": "success", "requested_date": "2026-07-24"}).status,
            "success",
        )
        for invalid in (
            {**single, "status": "degraded", "requested_date": "2026-07-24"},
            {**single, "status": "success", "requested_date": "2026-07-23"},
            {**single, "status": "success"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    WeatherResultsBlock.model_validate(invalid)

    def test_route_transit_extension_keeps_schema_v1_and_old_payload_compatibility(self):
        old_payload = route_block().model_dump(mode="json")
        for key in ("transit_type", "walking_distance_m", "legs", "alternatives"):
            old_payload["routes"][0].pop(key, None)
        old = RouteResultsBlock.model_validate(old_payload)
        self.assertEqual(old.schema_version, 1)
        self.assertIsNone(old.routes[0].transit_type)
        self.assertEqual(old.routes[0].legs, [])
        self.assertEqual(old.routes[0].alternatives, [])
        restored = deserialize_content_blocks([old_payload])
        self.assertIsInstance(restored[0], RouteResultsBlock)
        self.assertEqual(restored[0].routes[0].mode, "driving")

        transit = RouteOption(
            mode="transit",
            transit_type="subway",
            walking_distance_m=320,
            legs=[TransitLeg(kind="walking"), TransitLeg(kind="subway", line_name="地铁5号线")],
            alternatives=[TransitAlternative(transit_type="bus", legs=[TransitLeg(kind="bus", line_name="M201路")])],
        )
        self.assertEqual(transit.model_dump(mode="json")["transit_type"], "subway")

        with self.assertRaises(ValidationError):
            RouteOption(mode="transit", legs=[TransitLeg(kind="walking") for _ in range(9)])
        with self.assertRaises(ValidationError):
            RouteOption(
                mode="transit",
                alternatives=[TransitAlternative() for _ in range(3)],
            )

    def test_provider_neutral_blocks_serialize_with_schema_version(self):
        place = place_block()
        route = route_block()

        self.assertEqual(place.model_dump(mode="json")["schema_version"], 1)
        self.assertEqual(place.model_dump(mode="json")["places"][0]["provider_place_id"], "B0FFTEST")
        self.assertEqual(route.model_dump(mode="json")["routes"][0]["mode"], "driving")
        self.assertNotIn("location", route.model_dump(mode="json")["origin"])

        neutral_place = PlaceResultsBlock(
            type="place_results",
            id="neutral-place",
            schema_version=1,
            provider="future-map-provider",
            attribution=StructuredResultAttribution(label="城市地图服务"),
            query="咖啡",
            status="success",
            result_count=1,
            places=[
                PlaceResult(
                    name="未来咖啡店",
                    actions=[
                        StructuredResultAction(
                            kind="open_external",
                            label="打开地图",
                            url="https://maps.example.com/place/1",
                        )
                    ],
                )
            ],
        )
        serialized = neutral_place.model_dump(mode="json")
        self.assertEqual(serialized["attribution"], {"label": "城市地图服务"})
        self.assertEqual(serialized["places"][0]["actions"][0]["label"], "打开地图")

    def test_blocks_reject_unknown_fields_oversized_payloads_and_untrusted_urls(self):
        with self.assertRaises(ValidationError):
            PlaceResultsBlock.model_validate(
                {
                    **place_block().model_dump(mode="json"),
                    "provider_response": {"credential": "secret"},
                }
            )
        with self.assertRaises(ValidationError):
            place_block().model_copy(
                update={
                    "places": [
                        PlaceResult(provider_place_id=f"poi-{index}", name=f"地点-{index}") for index in range(6)
                    ]
                }
            ).__class__.model_validate(
                {
                    **place_block().model_dump(mode="json"),
                    "places": [{"provider_place_id": f"poi-{index}", "name": f"地点-{index}"} for index in range(6)],
                    "result_count": 6,
                }
            )
        with self.assertRaises(ValidationError):
            PlaceResult(
                provider_place_id="poi-1",
                name="地点",
                photos=[
                    PlacePhoto(url="https://store.is.autonavi.com/photo-1.jpg"),
                    PlacePhoto(url="https://store.is.autonavi.com/photo-2.jpg"),
                ],
            )
        with self.assertRaises(ValidationError):
            PlaceResult(
                provider_place_id="poi-1",
                name="地点",
                platform_url="https://evil.example/redirect?target=amap",
            )
        with self.assertRaises(ValidationError):
            PlacePhoto(url="http://store.is.autonavi.com/photo.jpg")
        generic_photo = PlacePhoto(url="https://images.example.com/place.jpg")
        self.assertEqual(generic_photo.url, "https://images.example.com/place.jpg")
        with self.assertRaises(ValidationError):
            StructuredResultAction(
                kind="open_external",
                label="不安全链接",
                url="http://maps.example.com/place/1",
            )
        with self.assertRaises(ValidationError):
            PlaceResult.model_validate(
                {
                    "provider_place_id": "poi-1",
                    "name": "地点",
                    "telephone": "0755-12345678",
                }
            )
        with self.assertRaises(ValidationError):
            PlaceResultsBlock.model_validate(
                {
                    **place_block().model_dump(mode="json"),
                    "limitations": ["x" * 100_000],
                }
            )


class ProductResultRecoveryTests(unittest.TestCase):
    def test_repository_hydration_and_continuation_restore_both_blocks(self):
        raw_blocks = [place_block().model_dump(mode="json"), route_block().model_dump(mode="json")]
        db_message = MessageModel(
            id="msg-results",
            conversation_id="conv-1",
            role="assistant",
            content=raw_blocks,
            created_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        message = ConversationRepository(None)._convert_message_to_schema(db_message)
        continuation = deserialize_content_blocks(raw_blocks)

        self.assertIsInstance(message.content[0], PlaceResultsBlock)
        self.assertIsInstance(message.content[1], RouteResultsBlock)
        self.assertEqual(continuation, message.content)

    def test_client_partial_merge_cannot_upsert_product_blocks(self):
        existing = [place_block(result_count=0).model_dump(mode="json"), route_block().model_dump(mode="json")]
        incoming_place = place_block().model_dump(mode="json")

        merged = merge_partial_content_blocks(existing, [incoming_place])

        self.assertEqual(merged, existing)

    def test_admin_projection_keeps_only_product_contract_fields(self):
        raw = place_block().model_dump(mode="json")
        raw.update(
            provider_response={"credential": "PRIVATE_SENTINEL"},
            raw_payload="PRIVATE_PAYLOAD",
        )

        projection = AdminAuditService._content_block_projection(raw)
        serialized = json.dumps(projection, ensure_ascii=False)

        self.assertEqual(projection["type"], "place_results")
        self.assertEqual(projection["schema_version"], 1)
        self.assertEqual(projection["places"][0]["provider_place_id"], "B0FFTEST")
        self.assertNotIn("provider_response", projection)
        self.assertNotIn("raw_payload", projection)
        self.assertNotIn("PRIVATE_SENTINEL", serialized)

        raw_route = route_block().model_dump(mode="json")
        raw_route["origin"]["location"] = "114.0,22.5"
        raw_route["routes"][0]["provider_response"] = {"credential": "ROUTE_PRIVATE_SENTINEL"}

        route_projection = AdminAuditService._content_block_projection(raw_route)
        route_serialized = json.dumps(route_projection, ensure_ascii=False)

        self.assertEqual(route_projection["type"], "route_results")
        self.assertEqual(route_projection["status"], "degraded")
        self.assertNotIn("location", route_projection["origin"])
        self.assertNotIn("provider_response", route_serialized)
        self.assertNotIn("ROUTE_PRIVATE_SENTINEL", route_serialized)

    def test_admin_projection_preserves_bounded_transit_structure(self):
        raw = route_block().model_dump(mode="json")
        raw["routes"] = [
            {
                "mode": "transit",
                "distance_m": 12_000,
                "duration_s": 2_400,
                "transit_type": "mixed",
                "walking_distance_m": 420,
                "transfers": 1,
                "legs": [
                    {"kind": "subway", "line_name": "地铁5号线", "departure_stop": "民治站"},
                    {"kind": "bus", "line_name": "M201路", "arrival_stop": "市民中心站"},
                ],
                "alternatives": [
                    {
                        "transit_type": "bus",
                        "distance_m": 13_000,
                        "duration_s": 2_700,
                        "walking_distance_m": 300,
                        "transfers": 0,
                        "summary": "公交备选",
                        "legs": [{"kind": "bus", "line_name": "M202路"}],
                    }
                ],
                "unsafe": "DROP_ME",
            }
        ]

        projection = AdminAuditService._content_block_projection(raw)

        route = projection["routes"][0]
        self.assertEqual(route["transit_type"], "mixed")
        self.assertEqual(route["legs"][0]["line_name"], "地铁5号线")
        self.assertEqual(route["alternatives"][0]["transit_type"], "bus")
        self.assertNotIn("distance_m", route["alternatives"][0])
        self.assertNotIn("unsafe", json.dumps(projection, ensure_ascii=False))


class ProductResultEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_block_upsert_event_uses_strict_whitelist_and_full_block(self):
        writer = AsyncMock()
        emitter = AgentEventEmitter(
            run_id="run-1",
            trace_id="trace-1",
            conversation_id="conv-1",
            task_id="task-1",
            redis_writer=writer,
        )
        block = place_block()

        await emitter.content_block_upserted(tool_call_id="tc-place", content_block=block)

        payload = writer.append_chunk.await_args.args[3]
        self.assertEqual(payload["type"], "content_block_upserted")
        self.assertEqual(payload["protocol_version"], 2)
        self.assertEqual(payload["tool_call_id"], "tc-place")
        self.assertEqual(payload["content_block"], block.model_dump(mode="json"))
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False).encode()), 65_536)

        with self.assertRaises(ValidationError):
            await emitter.content_block_upserted(
                tool_call_id="tc-place",
                content_block={
                    **block.model_dump(mode="json"),
                    "provider_response": {"secret": "LEAK_SENTINEL"},
                },
            )

    async def test_generated_image_block_is_upserted_and_round_trips_registry(self):
        from app.schemas.chat import GeneratedImageBlock
        from app.schemas.content_block_registry import deserialize_content_blocks

        writer = AsyncMock()
        emitter = AgentEventEmitter(
            run_id="run-1",
            trace_id="trace-1",
            conversation_id="conv-1",
            task_id="task-1",
            redis_writer=writer,
        )
        block = GeneratedImageBlock(
            type="generated_image",
            schema_version=2,
            provider="image",
            image_id="7ca2c0c7-4477-456c-8a3c-76960859f1bf",
            mime_type="image/jpeg",
            width=1024,
            height=1024,
            prompt="a red fox",
            aspect_ratio="1:1",
            model="gemini-3.1-flash-image-preview",
        )

        await emitter.content_block_upserted(tool_call_id="tc-img", content_block=block)

        payload = writer.append_chunk.await_args.args[3]
        self.assertEqual(payload["content_block"], block.model_dump(mode="json"))
        self.assertEqual(deserialize_content_blocks([block.model_dump(mode="json")]), [block])

    async def test_data_source_block_is_upserted_and_round_trips_registry(self):
        from app.schemas.chat import DataSourceBlock
        from app.schemas.content_block_registry import deserialize_content_blocks

        writer = AsyncMock()
        emitter = AgentEventEmitter(
            run_id="run-1",
            trace_id="trace-1",
            conversation_id="conv-1",
            task_id="task-1",
            redis_writer=writer,
        )
        block = DataSourceBlock(
            type="data_source",
            schema_version=1,
            category="map",
            provider="tencent_map",
            label="腾讯地图",
            tool_call_log_id="log-1",
        )

        await emitter.content_block_upserted(tool_call_id="tc-map", content_block=block)

        payload = writer.append_chunk.await_args.args[3]
        self.assertEqual(payload["content_block"], block.model_dump(mode="json"))
        self.assertEqual(deserialize_content_blocks([block.model_dump(mode="json")]), [block])

    async def test_document_block_is_upserted_for_live_document_card(self):
        from app.schemas.chat import DocumentBlock

        writer = AsyncMock()
        emitter = AgentEventEmitter(
            run_id="run-1",
            trace_id="trace-1",
            conversation_id="conv-1",
            task_id="task-1",
            redis_writer=writer,
        )
        block = DocumentBlock(
            type="document",
            schema_version=1,
            document_id="doc-1",
            version=1,
            title="青岛三日游攻略",
            format="markdown",
            operation="created",
            char_count=1200,
        )

        await emitter.content_block_upserted(tool_call_id="tc-doc", content_block=block)

        payload = writer.append_chunk.await_args.args[3]
        self.assertEqual(payload["type"], "content_block_upserted")
        self.assertEqual(payload["content_block"], block.model_dump(mode="json"))
