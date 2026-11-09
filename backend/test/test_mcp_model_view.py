import unittest

from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.model_view import build_mcp_model_view
from test.test_image_tools import IMAGE_TOOL, loaded_catalog
from test.test_mcp_agent_tools import FakeRepository, build_row


def generic_row(server_id, endpoint, count):
    names = [f"tool_{index}" for index in range(count)]
    return build_row(
        id=server_id,
        endpoint_url=endpoint,
        allowed_tools=names,
        discovered_tools=[{"name": name, "description": name, "input_schema": {"type": "object"}} for name in names],
    )


def view(rows, catalog=None):
    return build_mcp_model_view(
        object(),
        rows=rows,
        load_tools=lambda db, **kwargs: load_mcp_agent_tools(
            db,
            repository_factory=lambda _db: FakeRepository(rows),
            maps_hidden=lambda: True,
            gateway_catalog=catalog or loaded_catalog([]),
            **kwargs,
        ),
    )


class McpModelViewTests(unittest.TestCase):
    def test_generic_tools_listed_per_server_and_gateway_tools_are_not(self):
        rows = [
            generic_row("a-tencent", "https://mcp.map.qq.com/mcp", 12),
            generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1),
        ]

        # 生图来自 agent-tools 服务目录，不属于任何 MCP 服务行
        result = view(rows, loaded_catalog([IMAGE_TOOL]))

        tencent = result["servers"]["a-tencent"]["tools"]
        self.assertEqual(tencent[0], {"name": "tool_0", "label": tencent[0]["label"], "description": "tool_0"})
        self.assertEqual(len(tencent), 12)
        self.assertEqual(set(result["servers"]), {"a-tencent", "b-docs"})
        self.assertEqual(set(result), {"servers"})

    def test_disabled_server_has_no_model_tools(self):
        rows = [generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1)]
        rows[0].is_enabled = False

        result = view(rows)

        self.assertEqual(result["servers"]["b-docs"], {"tools": []})


if __name__ == "__main__":
    unittest.main()
