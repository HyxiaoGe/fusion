"""固定请求否定信号在字面分类与模型分类中的现有消费边界。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.stream.run_capability_model_classifier import classify_capability_request_with_model
from app.services.stream.run_capability_router import _extract_request_signals

_ALL_TOOLS = [
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
]


class TestRunCapabilityNetworkAuthorization(unittest.TestCase):
    def test_literal_path_keeps_network_denial_and_reauthorization(self) -> None:
        cases = (
            ("不要使用 web_search，OpenAI 最新新闻", (True, False, False), "clarification_only"),
            ("不要使用 url_read，读取 https://example.com/post", (False, True, False), "clarification_only"),
            ("本次请求不要联网，OpenAI 最新新闻", (True, True, True), "clarification_only"),
            ("不要使用 web_search，改为使用 web_search，OpenAI 最新新闻", (False, False, False), "fresh_web"),
        )
        for message, denied, package_id in cases:
            with self.subTest(message=message):
                request = _extract_request_signals(message)
                self.assertEqual(
                    (request.web_search_denied, request.url_read_denied, request.all_network_denied), denied
                )
                with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
                    candidate = classify_capability_request_with_model(message, _ALL_TOOLS)
                self.assertEqual(candidate.package_id, package_id)
                completion.assert_not_called()

    def test_model_path_receives_original_message_and_only_enforces_all_network_denial(self) -> None:
        cases = (
            ("不要使用 web_search，周末上海适合出门吗", (True, False, False), "verified_web"),
            ("不要使用 url_read，周末上海适合出门吗", (False, True, False), "verified_web"),
            ("本次请求不要联网，周末上海适合出门吗", (True, True, True), "clarification_only"),
            ("不要使用 web_search，改为使用 web_search，周末上海适合出门吗", (False, False, False), "verified_web"),
        )
        # 模拟模型返回两个联网工具，区分服务端限制与模型自行服从否定指令。
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"package_id": "verified_web", "explicit_tool_names": ["web_search", "url_read"]}
                        )
                    )
                )
            ]
        )
        for message, denied, package_id in cases:
            with self.subTest(message=message):
                request = _extract_request_signals(message)
                self.assertEqual(
                    (request.web_search_denied, request.url_read_denied, request.all_network_denied), denied
                )
                self.assertNotEqual(request.routing_message, message)
                with (
                    patch("app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY", "test-key"),
                    patch(
                        "app.services.stream.run_capability_model_classifier.litellm.completion", return_value=response
                    ) as completion,
                ):
                    candidate = classify_capability_request_with_model(message, _ALL_TOOLS)
                completion.assert_called_once()
                self.assertEqual(completion.call_args.kwargs["messages"][-1], {"role": "user", "content": message})
                self.assertEqual(candidate.package_id, package_id)
                self.assertEqual(candidate.explicit_tool_names, None if denied[2] else ("web_search", "url_read"))
