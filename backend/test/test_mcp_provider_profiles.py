import unittest

from app.services.mcp.provider_profiles import (
    AMAP_READ_ONLY_TOOL_ALLOWLIST,
    CONTEXT7_READ_ONLY_TOOL_ALLOWLIST,
    amap_gateway_endpoint,
    context7_gateway_endpoint,
    endpoint_auth_binding_is_allowed,
    endpoint_timeout_floors,
    endpoint_tool_allowlist,
    endpoint_tool_guidance,
    endpoint_tool_schema_override,
    is_official_amap_endpoint,
    is_official_context7_endpoint,
    tool_is_allowed_for_endpoint,
)

AMAP_GATEWAY = "http://agent-tools-gateway:8100/amap/mcp"
CONTEXT7_GATEWAY = "http://agent-tools-gateway:8100/context7/mcp"


class McpProviderProfileTests(unittest.TestCase):
    def test_amap_is_recognized_only_at_the_agent_tools_gateway_endpoint(self):
        self.assertEqual(amap_gateway_endpoint(), AMAP_GATEWAY)
        self.assertTrue(is_official_amap_endpoint(AMAP_GATEWAY))
        self.assertTrue(is_official_amap_endpoint(AMAP_GATEWAY + "/"))
        self.assertFalse(is_official_amap_endpoint("https://mcp.amap.com/mcp"))
        self.assertFalse(is_official_amap_endpoint("http://agent-tools-gateway:8100/trends/mcp"))
        self.assertFalse(is_official_amap_endpoint("http://agent-tools-gateway:8100/amap/mcp/extra"))

        self.assertEqual(endpoint_tool_allowlist(AMAP_GATEWAY), AMAP_READ_ONLY_TOOL_ALLOWLIST)
        self.assertIn("maps_coordinate_convert", AMAP_READ_ONLY_TOOL_ALLOWLIST)
        self.assertTrue(tool_is_allowed_for_endpoint(AMAP_GATEWAY, "maps_geo"))
        self.assertFalse(tool_is_allowed_for_endpoint(AMAP_GATEWAY, "maps_schema_navi"))
        self.assertNotEqual(endpoint_tool_guidance(AMAP_GATEWAY, "maps_geo"), "")
        # 直连高德的行不再享有产品化处理，也不再带可信约束。
        self.assertIsNone(endpoint_tool_allowlist("https://mcp.amap.com/mcp"))
        self.assertEqual(endpoint_tool_guidance("https://mcp.amap.com/mcp", "maps_geo"), "")

    def test_agent_tools_credential_only_goes_to_the_gateway_as_bearer(self):
        self.assertTrue(
            endpoint_auth_binding_is_allowed(
                AMAP_GATEWAY, auth_type="bearer", auth_name=None, credential_ref="AGENT_TOOLS_API_KEY"
            )
        )
        self.assertTrue(
            endpoint_auth_binding_is_allowed(
                "http://agent-tools-gateway:8100/image/mcp",
                auth_type="bearer",
                auth_name=None,
                credential_ref="AGENT_TOOLS_API_KEY",
            )
        )
        for endpoint, auth_type, auth_name, credential_ref in (
            (AMAP_GATEWAY, "query", "key", "AGENT_TOOLS_API_KEY"),
            (AMAP_GATEWAY, "bearer", None, "AMAP_MCP_API_KEY"),
            (AMAP_GATEWAY, "none", None, None),
            ("https://mcp.amap.com/mcp", "bearer", None, "AGENT_TOOLS_API_KEY"),
            ("http://agent-tools-gateway.evil.example/amap/mcp", "bearer", None, "AGENT_TOOLS_API_KEY"),
        ):
            with self.subTest(endpoint=endpoint, auth_type=auth_type, credential_ref=credential_ref):
                self.assertFalse(
                    endpoint_auth_binding_is_allowed(
                        endpoint, auth_type=auth_type, auth_name=auth_name, credential_ref=credential_ref
                    )
                )

    def test_context7_is_recognized_only_at_the_agent_tools_gateway_endpoint(self):
        self.assertEqual(context7_gateway_endpoint(), CONTEXT7_GATEWAY)
        self.assertTrue(is_official_context7_endpoint(CONTEXT7_GATEWAY))
        self.assertTrue(is_official_context7_endpoint(CONTEXT7_GATEWAY + "/"))
        self.assertFalse(is_official_context7_endpoint("https://mcp.context7.com/mcp"))
        self.assertFalse(is_official_context7_endpoint(AMAP_GATEWAY))

        self.assertEqual(endpoint_tool_allowlist(CONTEXT7_GATEWAY + "/"), CONTEXT7_READ_ONLY_TOOL_ALLOWLIST)
        self.assertTrue(tool_is_allowed_for_endpoint(CONTEXT7_GATEWAY, "resolve-library-id"))
        self.assertTrue(tool_is_allowed_for_endpoint(CONTEXT7_GATEWAY, "query-docs"))
        self.assertFalse(tool_is_allowed_for_endpoint(CONTEXT7_GATEWAY, "future-write-tool"))
        self.assertEqual(endpoint_timeout_floors(CONTEXT7_GATEWAY), (20.0, 30.0, 45.0))
        # 直连 Context7 的行不再享有产品化处理：无白名单、无可信约束、无 schema 覆盖、无超时下限。
        direct = "https://mcp.context7.com/mcp"
        self.assertIsNone(endpoint_tool_allowlist(direct))
        self.assertEqual(endpoint_tool_guidance(direct, "query-docs"), "")
        self.assertIsNone(endpoint_tool_schema_override(direct, "query-docs"))
        self.assertIsNone(endpoint_timeout_floors(direct))

    def test_context7_tools_have_trusted_guidance_and_fixed_schemas(self):
        endpoint = CONTEXT7_GATEWAY

        resolve_guidance = endpoint_tool_guidance(endpoint, "resolve-library-id")
        query_guidance = endpoint_tool_guidance(endpoint, "query-docs")
        self.assertIn("public software library", resolve_guidance)
        self.assertIn("Do not send", resolve_guidance)
        self.assertIn("resolve-library-id", query_guidance)
        self.assertIn("Call resolve-library-id first", query_guidance)
        self.assertIn("Do not send", query_guidance)

        self.assertEqual(
            endpoint_tool_schema_override(endpoint, "resolve-library-id"),
            {
                "type": "object",
                "properties": {
                    "libraryName": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                },
                "required": ["libraryName", "query"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            endpoint_tool_schema_override(endpoint, "query-docs"),
            {
                "type": "object",
                "properties": {
                    "libraryId": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 200,
                    },
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                },
                "required": ["libraryId", "query"],
                "additionalProperties": False,
            },
        )

    def test_non_profiled_endpoint_has_no_trusted_guidance_or_schema_override(self):
        endpoint = "https://example.com/mcp"

        self.assertIsNone(endpoint_tool_allowlist(endpoint))
        self.assertEqual(endpoint_tool_guidance(endpoint, "query-docs"), "")
        self.assertIsNone(endpoint_tool_schema_override(endpoint, "query-docs"))

    def test_gateway_endpoints_only_accept_the_product_bearer_credential(self):
        self.assertTrue(
            endpoint_auth_binding_is_allowed(
                CONTEXT7_GATEWAY, auth_type="bearer", auth_name=None, credential_ref="AGENT_TOOLS_API_KEY"
            )
        )
        # 网关上的任何服务都不接受免密或其他凭证；非网关端点不受本规则约束。
        for auth_type, auth_name, credential_ref in (
            ("none", None, None),
            ("header", "CONTEXT7_API_KEY", "CONTEXT7_API_KEY"),
            ("bearer", None, "DASHSCOPE_API_KEY"),
        ):
            with self.subTest(auth_type=auth_type, credential_ref=credential_ref):
                self.assertFalse(
                    endpoint_auth_binding_is_allowed(
                        CONTEXT7_GATEWAY, auth_type=auth_type, auth_name=auth_name, credential_ref=credential_ref
                    )
                )
        self.assertTrue(
            endpoint_auth_binding_is_allowed(
                "https://mcp.context7.com/mcp",
                auth_type="header",
                auth_name="CONTEXT7_API_KEY",
                credential_ref="CONTEXT7_API_KEY",
            )
        )


if __name__ == "__main__":
    unittest.main()
