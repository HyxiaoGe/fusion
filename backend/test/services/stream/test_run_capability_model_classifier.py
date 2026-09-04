from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.services.stream.run_capability_model_classifier import classify_capability_request_with_model

ALL_TOOLS = [
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
]


@pytest.fixture(autouse=True)
def _classifier_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY", "test-key")


def _completion_response(package_id: str, explicit_tool_names: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "package_id": package_id,
                            "explicit_tool_names": explicit_tool_names or [],
                        }
                    )
                )
            )
        ]
    )


def _assert_clarification(candidate) -> None:
    assert candidate.package_id == "clarification_only"
    assert candidate.confidence == "low"
    assert candidate.reason_codes == ("insufficient_capability_signal",)
    assert candidate.resolution_mode == "clarification"
    assert candidate.explicit_tool_names is None


def test_literal_hit_returns_without_model_call() -> None:
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "把 See you tomorrow 翻译成中文",
            ALL_TOOLS,
        )

    assert candidate.package_id == "transform"
    completion.assert_not_called()


def test_model_call_is_single_bounded_and_maps_weather() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("weather", ["weather_forecast"]),
    ) as completion:
        candidate = classify_capability_request_with_model("周末上海适合出门吗？", ALL_TOOLS)

    assert candidate.package_id == "weather"
    assert candidate.explicit_tool_names == ("weather_forecast",)
    assert candidate.include_current_date is True
    completion.assert_called_once()
    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == "litellm_proxy/deepseek-chat"
    assert kwargs["timeout"] == 1.5
    assert kwargs["num_retries"] == 0
    assert kwargs["max_tokens"] == 128
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["api_base"]
    assert kwargs["extra_body"]["metadata"]["tags"] == ["app:fusion", "phase:run_capability_classifier"]


def test_model_output_maps_mixed_itinerary_in_canonical_tool_order() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response(
            "mixed_itinerary",
            ["search_flights", "weather_forecast", "route_compare"],
        ),
    ):
        candidate = classify_capability_request_with_model("帮我安排一个周末出行方案", ALL_TOOLS)

    assert candidate.package_id == "mixed_itinerary"
    assert candidate.explicit_tool_names == ("weather_forecast", "route_compare", "search_flights")
    assert candidate.include_current_date is True


def test_only_most_recent_complete_turn_is_sent_to_model() -> None:
    conversation_messages = [
        {"role": "user", "content": "过早的用户消息"},
        {"role": "assistant", "content": "过早的助手回复"},
        {"role": "user", "content": "最近完整轮次的用户消息"},
        {"role": "assistant", "content": "最近完整轮次的助手回复"},
        {"role": "user", "content": "不完整轮次"},
    ]
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("direct"),
    ) as completion:
        classify_capability_request_with_model("当前消息", ALL_TOOLS, conversation_messages)

    rendered_messages = completion.call_args.kwargs["messages"]
    rendered_context = "\n".join(item["content"] for item in rendered_messages)
    assert "最近完整轮次的用户消息" in rendered_context
    assert "最近完整轮次的助手回复" in rendered_context
    assert "当前消息" in rendered_context
    assert "过早的用户消息" not in rendered_context
    assert "不完整轮次" not in rendered_context


def test_context_is_dropped_before_current_message_when_input_budget_is_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS",
        100,
    )
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("direct"),
    ) as completion:
        candidate = classify_capability_request_with_model(
            "当前短消息",
            ALL_TOOLS,
            [
                {"role": "user", "content": "历史消息" * 30},
                {"role": "assistant", "content": "历史回复" * 30},
            ],
        )

    assert candidate.package_id == "direct"
    rendered_context = "\n".join(item["content"] for item in completion.call_args.kwargs["messages"])
    assert "历史消息" not in rendered_context
    assert "当前短消息" in rendered_context


def test_current_message_over_input_budget_fails_closed_without_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS",
        100,
    )
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model("超长消息" * 100, ALL_TOOLS)

    _assert_clarification(candidate)
    completion.assert_not_called()


def test_missing_credentials_fails_closed_without_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY", "")
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_clarification(candidate)
    completion.assert_not_called()


@pytest.mark.parametrize(
    "response_or_error",
    [
        TimeoutError("deadline"),
        RuntimeError("proxy unavailable"),
        _completion_response("unknown_package"),
        _completion_response("weather", ["web_search"]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]),
    ],
    ids=["timeout", "exception", "unknown-package", "illegal-tool-combination", "malformed-json"],
)
def test_model_failures_and_invalid_output_fail_closed_without_retry(response_or_error) -> None:
    completion = Mock(
        side_effect=response_or_error if isinstance(response_or_error, BaseException) else None,
        return_value=None if isinstance(response_or_error, BaseException) else response_or_error,
    )
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_clarification(candidate)
    completion.assert_called_once()


def test_global_network_denial_cannot_be_promoted_by_model() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("fresh_web", ["web_search"]),
    ) as completion:
        candidate = classify_capability_request_with_model("请完全离线回答，不要联网搜索今天的新闻", ALL_TOOLS)

    _assert_clarification(candidate)
    completion.assert_called_once()


def test_classifier_log_does_not_contain_raw_message() -> None:
    raw_message = "不要记录的秘密请求内容"
    with (
        patch(
            "app.services.stream.run_capability_model_classifier.litellm.completion",
            return_value=_completion_response("direct"),
        ),
        patch("app.services.stream.run_capability_model_classifier.logger.info") as log_info,
    ):
        classify_capability_request_with_model(raw_message, ALL_TOOLS)

    assert raw_message not in repr(log_info.call_args_list)
