import unittest

from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_REMOTE_DEPENDENCIES
from app.services.mcp.model_view import build_mcp_model_view
from app.services.mcp.tool_deferral import MAX_DIRECT_MCP_TOOLS
from test.test_mcp_agent_tools import FakeRepository, build_row

AMAP_NAMES = sorted(set().union(*AMAP_PRODUCT_REMOTE_DEPENDENCIES.values()))


def amap_row():
    return build_row(
        id="z-amap",
        name="高德地图",
        provider="amap",
        endpoint_url="https://mcp.amap.com/mcp",
        allowed_tools=AMAP_NAMES,
        discovered_tools=[
            {"name": name, "description": name, "input_schema": {"type": "object"}} for name in AMAP_NAMES
        ],
    )


def generic_row(server_id, endpoint, count):
    names = [f"tool_{index}" for index in range(count)]
    return build_row(
        id=server_id,
        endpoint_url=endpoint,
        allowed_tools=names,
        discovered_tools=[{"name": name, "description": name, "input_schema": {"type": "object"}} for name in names],
    )


def view(rows, quota=None):
    quota = quota or {}
    return build_mcp_model_view(
        object(),
        rows=rows,
        load_tools=lambda db, **kwargs: load_mcp_agent_tools(
            db, repository_factory=lambda _db: FakeRepository(rows), **kwargs
        ),
        read_quota=lambda server_id, groups: {g: s for g, s in quota.get(server_id, {}).items() if g in groups},
    )


class McpModelViewTests(unittest.TestCase):
    def test_products_direct_and_many_generic_tools_on_demand(self):
        rows = [
            amap_row(),
            generic_row("a-tencent", "https://mcp.map.qq.com/mcp", MAX_DIRECT_MCP_TOOLS),
            generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1),
        ]

        result = view(rows)

        amap = result["servers"]["z-amap"]
        self.assertEqual([tool["name"] for tool in amap["tools"]], ["local_place_search", "route_compare"])
        self.assertEqual({tool["mode"] for tool in amap["tools"]}, {"direct"})
        self.assertEqual(
            amap["tools"][0]["source_tools"], sorted(AMAP_PRODUCT_REMOTE_DEPENDENCIES["local_place_search"])
        )
        self.assertEqual(amap["hidden_tools"], [])
        self.assertIn("关键词", amap["tools"][0]["description"])
        tencent = result["servers"]["a-tencent"]["tools"]
        self.assertEqual(tencent[0]["name"], "tool_0")
        self.assertEqual(tencent[0]["description"], "tool_0")
        self.assertEqual({tool["mode"] for tool in tencent}, {"on_demand"})
        self.assertTrue(result["deferral"]["on_demand"])
        self.assertEqual(result["deferral"]["generic_tool_count"], MAX_DIRECT_MCP_TOOLS + 1)

    def test_quota_exhausted_hides_search_and_map_tools_go_direct(self):
        rows = [
            amap_row(),
            generic_row("a-tencent", "https://mcp.map.qq.com/mcp", MAX_DIRECT_MCP_TOOLS),
            generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1),
        ]

        result = view(rows, quota={"z-amap": {"search": 3600}})

        amap = result["servers"]["z-amap"]
        self.assertEqual([tool["name"] for tool in amap["tools"]], ["route_compare"])
        self.assertEqual(
            amap["hidden_tools"],
            [
                {
                    "name": "local_place_search",
                    "label": "高德地点搜索",
                    "description": amap["hidden_tools"][0]["description"],
                    "reason": "quota_exhausted",
                    "resets_in_seconds": 3600,
                }
            ],
        )
        self.assertIn("关键词", amap["hidden_tools"][0]["description"])
        self.assertEqual(amap["quota_exhausted"], [{"group": "search", "resets_in_seconds": 3600}])
        self.assertEqual({tool["mode"] for tool in result["servers"]["a-tencent"]["tools"]}, {"direct"})
        self.assertEqual(result["deferral"]["generic_tool_count"], 1)
        self.assertFalse(result["deferral"]["on_demand"])

    def test_disabled_server_has_no_model_tools(self):
        rows = [generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1)]
        rows[0].is_enabled = False

        result = view(rows)

        self.assertEqual(result["servers"]["b-docs"], {"tools": [], "hidden_tools": []})


if __name__ == "__main__":
    unittest.main()
