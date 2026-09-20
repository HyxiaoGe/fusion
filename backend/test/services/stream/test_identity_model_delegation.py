"""固定身份句委派边界及录制的既有模型失败；回放不代表新的模型正确性实测。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.stream.run_capability_model_classifier import classify_capability_request_with_model
from app.services.stream.run_capability_request_signals import _extract_request_signals
from app.services.stream.run_capability_router import _classify_literal_layer

_TOOLS = [
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
]

# 原四提交新增的历史纯身份句；保留原句，改由模型承担礼貌与情态包装。
_DEFERRED_HISTORICAL_IDENTITY = (
    "请介绍一下你自己",
    "你好，请问你是谁？",
    "请问一下，你是谁？",
    "可以介绍一下你自己吗？",
    "麻烦你介绍一下你自己",
    "能介绍一下你自己吗？",
    "能否介绍一下你自己？",
    "可以请你介绍一下你自己吗？",
    "可否告诉我你叫什么名字？",
    "是否可以请介绍一下你自己？",
    "能不能请问一下你是谁呀？",
)

# #93 独立 fixture 的五句；末两句不是助手身份请求，不能被身份字面短路。
_INDEPENDENT_IDENTITY = (
    "可以先告诉我你是谁吗？",
    "麻烦你介绍一下自己吧，我还没用过这个助手。",
    "你是哪个团队做出来的呀？",
    "你觉得我适合做什么？",
    "你能告诉我怎么介绍自己才不尴尬吗？",
)


class IdentityModelDelegationTests(unittest.TestCase):
    def _classify_recorded_response(self, message: str, content: str):
        self.assertIsNone(_classify_literal_layer(_extract_request_signals(message), _TOOLS))
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        observed = Mock()
        with (
            patch("app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY", "test-key"),
            patch(
                "app.services.stream.run_capability_model_classifier.litellm.completion", return_value=response
            ) as completion,
        ):
            route = classify_capability_request_with_model(message, _TOOLS, result_callback=observed)
        completion.assert_called_once()
        self.assertEqual(completion.call_args.kwargs["messages"][-1], {"role": "user", "content": message})
        return route, observed

    def test_historical_polite_identity_requests_delegate_to_model(self):
        for message in _DEFERRED_HISTORICAL_IDENTITY:
            with self.subTest(message=message):
                route, observed = self._classify_recorded_response(
                    message, '{"package_id":"direct","explicit_tool_names":[]}'
                )
                self.assertEqual(route.package_id, "direct")
                observed.assert_called_once_with("model", None)

    def test_independent_identity_and_nonidentity_advice_do_not_short_circuit(self):
        for message in _INDEPENDENT_IDENTITY:
            with self.subTest(message=message):
                route, observed = self._classify_recorded_response(
                    message, '{"package_id":"direct","explicit_tool_names":[]}'
                )
                self.assertEqual(route.package_id, "direct")
                observed.assert_called_once_with("model", None)

    def test_recorded_existing_compound_failures_preserve_failure_classification(self):
        # 六条是已知未满足业务期望的真实响应记录；通过回放只说明失败行为未变。
        fixture = Path(__file__).parents[2] / "fixtures" / "identity_classifier_observed_failures.json"
        for case in json.loads(fixture.read_text())["cases"]:
            with self.subTest(case=case["id"]):
                route, observed = self._classify_recorded_response(case["question"], case["recorded_response_content"])
                self.assertEqual(route.package_id, "clarification_only")
                self.assertEqual(route.package_id, case["observed_package"])
                self.assertNotIn(route.package_id, case["expected_business_packages"])
                observed.assert_called_once_with(case["observed_layer"], case["observed_error_type"])
                self.assertEqual(route.reason_codes, ("insufficient_capability_signal",))
                self.assertIsNone(route.explicit_tool_names)
