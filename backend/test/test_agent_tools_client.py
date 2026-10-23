"""agent-tools 网关请求头与生图图片地址"""

import unittest
from unittest.mock import patch

from app.core.logger import log_context
from app.services.external.agent_tools import agent_tools_headers, agent_tools_url
from app.services.mcp.image_service_tools import internal_image_url


class AgentToolsHeadersTests(unittest.TestCase):
    def test_run_context_supplies_user_and_request_id(self):
        with (
            patch("app.services.external.agent_tools.settings.AGENT_TOOLS_API_KEY", "svc-key"),
            log_context(run_id="run-1", user_id="user-1"),
        ):
            headers = agent_tools_headers("web_search")
        self.assertEqual(
            headers,
            {
                "Authorization": "Bearer svc-key",
                "X-Caller-Scene": "web_search",
                "X-Caller-User": "user-1",
                "X-Request-ID": "run-1",
            },
        )

    def test_outside_a_run_only_declares_scene(self):
        headers = agent_tools_headers("admin_usage")
        self.assertEqual(set(headers), {"Authorization", "X-Caller-Scene"})

    def test_url_is_prefixed_by_service(self):
        with patch("app.services.external.agent_tools.settings.AGENT_TOOLS_URL", "http://gw:8100/"):
            self.assertEqual(agent_tools_url("reader", "/read"), "http://gw:8100/reader/read")


class InternalImageUrlTests(unittest.TestCase):
    def test_gateway_endpoint_keeps_service_prefix(self):
        self.assertEqual(
            internal_image_url("http://agent-tools-gateway:8100/image/mcp", "/static/images/a.png"),
            "http://agent-tools-gateway:8100/image/static/images/a.png",
        )

    def test_root_endpoint_downloads_from_origin(self):
        self.assertEqual(
            internal_image_url("http://192.168.1.11:8090/mcp", "/static/images/a.png"),
            "http://192.168.1.11:8090/static/images/a.png",
        )
