import unittest

from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.image_service_tools import IMAGE_SERVICE_REMOTE_TOOL_NAME
from app.services.mcp.model_view import build_mcp_model_view
from test.test_mcp_agent_tools import FakeRepository, build_row


def image_row():
    return build_row(
        id="z-image",
        name="生图",
        provider="image-service",
        endpoint_url="http://agent-tools-gateway:8100/image/mcp",
        allowed_tools=[IMAGE_SERVICE_REMOTE_TOOL_NAME],
        discovered_tools=[
            {"name": IMAGE_SERVICE_REMOTE_TOOL_NAME, "description": "draw", "input_schema": {"type": "object"}}
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


def view(rows):
    return build_mcp_model_view(
        object(),
        rows=rows,
        load_tools=lambda db, **kwargs: load_mcp_agent_tools(
            db, repository_factory=lambda _db: FakeRepository(rows), maps_hidden=lambda: True, **kwargs
        ),
    )


class McpModelViewTests(unittest.TestCase):
    def test_products_and_generic_tools_listed_per_server(self):
        rows = [
            image_row(),
            generic_row("a-tencent", "https://mcp.map.qq.com/mcp", 12),
            generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1),
        ]

        result = view(rows)

        image = result["servers"]["z-image"]
        self.assertEqual([tool["name"] for tool in image["tools"]], ["generate_image"])
        self.assertEqual(image["tools"][0]["kind"], "product")
        self.assertEqual(image["tools"][0]["source_tools"], [IMAGE_SERVICE_REMOTE_TOOL_NAME])
        self.assertIn("生成图片", image["tools"][0]["description"])
        tencent = result["servers"]["a-tencent"]["tools"]
        self.assertEqual(tencent[0]["name"], "tool_0")
        self.assertEqual(tencent[0]["description"], "tool_0")
        self.assertEqual(len(tencent), 12)
        self.assertEqual(set(result), {"servers"})

    def test_disabled_server_has_no_model_tools(self):
        rows = [generic_row("b-docs", "https://learn.microsoft.com/api/mcp", 1)]
        rows[0].is_enabled = False

        result = view(rows)

        self.assertEqual(result["servers"]["b-docs"], {"tools": []})


if __name__ == "__main__":
    unittest.main()
