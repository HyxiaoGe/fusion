from unittest import IsolatedAsyncioTestCase

from app.services.agent.context_broker import Geolocation
from app.services.mcp.amap_coordinate_converter import (
    COORDINATE_CONVERT_TOOL,
    AmapCoordinateConversionError,
    build_remote_coordinate_converter,
    parse_converted_coordinate,
)
from app.services.mcp.client import McpClientError


class FakeExecutor:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def call(self, remote_tool_name, expected_definition_sha256, arguments):
        self.calls.append((remote_tool_name, expected_definition_sha256, arguments))
        if self.error is not None:
            raise self.error
        return self.payload


LOCATION = Geolocation(latitude=22.7654321, longitude=114.1234567, accuracy_m=10, acquired_at=1_700_000_000)


class AmapCoordinateConverterTests(IsolatedAsyncioTestCase):
    async def test_calls_proxy_tool_with_authorized_hash_and_six_decimal_coordinates(self):
        executor = FakeExecutor({"content": [{"type": "text", "text": "114.128765,22.762345"}], "isError": False})
        convert = build_remote_coordinate_converter(executor, {COORDINATE_CONVERT_TOOL: "hash-convert"})

        result = await convert(LOCATION)

        self.assertEqual(result, "114.128765,22.762345")
        self.assertEqual(
            executor.calls,
            [(COORDINATE_CONVERT_TOOL, "hash-convert", {"locations": "114.123457,22.765432", "coordsys": "gps"})],
        )

    async def test_missing_authorization_fails_before_any_remote_call(self):
        executor = FakeExecutor()
        convert = build_remote_coordinate_converter(executor, {"maps_geo": "hash-geo"})

        with self.assertRaises(AmapCoordinateConversionError) as raised:
            await convert(LOCATION)

        self.assertEqual(str(raised.exception), "高德坐标转换不可用")
        self.assertEqual(executor.calls, [])

    async def test_remote_failure_is_wrapped_without_upstream_details(self):
        executor = FakeExecutor(error=McpClientError("tool_error", "USER_DAILY_QUERY_OVER_LIMIT secret-detail"))
        convert = build_remote_coordinate_converter(executor, {COORDINATE_CONVERT_TOOL: "hash-convert"})

        with self.assertRaises(AmapCoordinateConversionError) as raised:
            await convert(LOCATION)

        self.assertNotIn("secret-detail", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_parses_text_or_structured_result_and_rejects_everything_else(self):
        self.assertEqual(
            parse_converted_coordinate({"content": [{"type": "text", "text": " 114.1,22.7 "}]}),
            "114.100000,22.700000",
        )
        self.assertEqual(
            parse_converted_coordinate({"content": [], "structuredContent": {"result": "114.1,22.7"}}),
            "114.100000,22.700000",
        )
        for payload in (
            None,
            {"content": [{"type": "text", "text": "114.1,22.7"}], "isError": True},
            {"content": [{"type": "text", "text": "not a coordinate"}]},
            {"content": [{"type": "text", "text": "200.0,22.7"}]},
            {"content": [{"type": "text", "text": "114.1,95.0"}]},
            {"content": [{"type": "text", "text": "114.1,22.7;115.0,23.0"}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(AmapCoordinateConversionError):
                parse_converted_coordinate(payload)
