from __future__ import annotations

import json
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import Mock, patch

import litellm
import pytest
from pydantic import ValidationError

from app.db.models import Message
from app.services.stream.run_capability_model_classifier import (
    _build_messages,
    _most_recent_complete_turn,
    _parse_model_route,
    classify_capability_request_with_model,
)

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
            token_counter_fn=lambda *, messages, **_kwargs: 101 if len(messages) == 4 else 20,
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


@pytest.mark.parametrize(
    "message",
    ["中" * 2000, "word " * 2000, "🚀" * 2000],
    ids=["中文", "英文", "emoji"],
)
def test_real_token_counter_hard_limit_blocks_chinese_english_and_emoji(message: str) -> None:
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            message,
            ALL_TOOLS,
            token_counter_fn=litellm.token_counter,
        )

    _assert_clarification(candidate)
    completion.assert_not_called()


def test_token_counter_uses_classifier_model_family() -> None:
    token_counter = Mock(return_value=2001)
    classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS, token_counter_fn=token_counter)

    assert token_counter.call_args.kwargs["model"] == "deepseek/deepseek-chat"


def test_token_counter_failure_fails_closed_without_model_call() -> None:
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            token_counter_fn=Mock(side_effect=RuntimeError("tokenizer unavailable")),
        )

    _assert_clarification(candidate)
    completion.assert_not_called()


def test_token_budget_drops_history_before_rejecting_current_message() -> None:
    def token_counter(*, messages, **_kwargs) -> int:
        return 2001 if any("旧轮次" in item["content"] for item in messages) else 20

    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("direct"),
    ) as completion:
        candidate = classify_capability_request_with_model(
            "当前消息",
            ALL_TOOLS,
            [
                {"role": "user", "content": "旧轮次用户"},
                {"role": "assistant", "content": "旧轮次助手"},
            ],
            token_counter_fn=token_counter,
        )

    assert candidate.package_id == "direct"
    assert "旧轮次" not in "\n".join(item["content"] for item in completion.call_args.kwargs["messages"])


def test_orm_content_blocks_keep_only_latest_complete_user_assistant_turn() -> None:
    messages = [
        Message(role="user", content=[{"type": "text", "text": "较早用户"}]),
        Message(role="assistant", content=[{"type": "text", "text": "较早助手"}]),
        Message(
            role="user",
            content=[{"type": "text", "text": "最近用户"}, {"type": "file", "file_id": "file-1"}],
        ),
        Message(
            role="assistant",
            content=[{"type": "thinking", "thinking": "不应投影"}, {"type": "text", "text": "最近助手"}],
        ),
        Message(role="user", content=[{"type": "text", "text": "当前未完成用户"}]),
    ]

    turn = _most_recent_complete_turn(messages)

    assert turn == [
        {"role": "user", "content": "最近用户"},
        {"role": "assistant", "content": "最近助手"},
    ]


def test_system_prompt_defines_taxonomy_tool_mapping_order_and_negative_boundaries() -> None:
    messages = _build_messages(
        "当前消息",
        ALL_TOOLS,
        None,
        token_counter_fn=lambda **_kwargs: 1,
    )

    assert messages is not None
    prompt = messages[0]["content"]
    assert "fresh_web：最新或当前外部事实" in prompt
    assert "verified_web：要求官方或可靠来源" in prompt
    assert "url_read：给定 URL 的读取或总结" in prompt
    assert "mobility_route：明确同城路线" in prompt
    assert "mobility_intercity：跨城起终点但方式不明确" in prompt
    assert "travel_air_rail：仅比较航班与火车" in prompt
    assert "mixed_itinerary：2–3 个不同产品族" in prompt
    assert "canonical order" in prompt
    assert "禁止选择 deep_research" in prompt
    assert "全局禁网时不得选择任何外部工具" in prompt


def test_missing_explicit_tool_names_is_rejected() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"package_id":"direct"}'))]
    )

    with pytest.raises(ValidationError):
        _parse_model_route(response, False, ALL_TOOLS, include_current_date=False)


@pytest.mark.parametrize(
    "tools,is_valid",
    [
        (tools, tools != ("search_flights", "search_trains"))
        for count in (2, 3)
        for tools in combinations(
            ("weather_forecast", "local_place_search", "route_compare", "search_flights", "search_trains"),
            count,
        )
    ]
    + [
        (("search_flights",), False),
        (("weather_forecast", "weather_forecast"), False),
        (("weather_forecast", "local_place_search", "route_compare", "search_flights"), False),
    ],
)
def test_mixed_itinerary_requires_two_or_three_product_families_except_air_rail_pair(
    tools: tuple[str, ...], is_valid: bool
) -> None:
    result = _parse_model_route(
        _completion_response("mixed_itinerary", list(tools)),
        False,
        ALL_TOOLS,
        include_current_date=False,
    )

    assert (result is not None) is is_valid


@pytest.mark.parametrize(
    (
        "package_id",
        "tools",
        "request_has_relative_date",
        "expected_reason_codes",
        "expected_include_date",
    ),
    [
        ("direct", (), False, ("stable_knowledge_question",), False),
        ("transform", (), False, ("text_transform_request",), False),
        ("date", (), False, ("current_date_question",), True),
        ("fresh_web", ("web_search",), False, ("fresh_external_fact",), True),
        ("verified_web", ("web_search", "url_read"), False, ("verified_source_request",), True),
        ("url_read", ("url_read",), False, ("explicit_url_read",), False),
        ("weather", ("weather_forecast",), False, ("explicit_weather_request",), True),
        ("place_discovery", ("local_place_search",), False, ("explicit_place_discovery",), False),
        ("mobility_route", ("route_compare",), False, ("explicit_route_task",), False),
        ("mobility_route", ("route_compare",), True, ("explicit_route_task",), True),
        ("flight", ("search_flights",), False, ("explicit_flight_request",), True),
        ("train", ("search_trains",), False, ("explicit_train_request",), True),
        ("travel_air_rail", ("search_flights", "search_trains"), False, ("air_rail_comparison",), True),
        (
            "mobility_intercity",
            ("route_compare", "search_flights", "search_trains"),
            False,
            ("origin_destination_relation", "intercity_locations"),
            True,
        ),
        (
            "mixed_itinerary",
            ("weather_forecast", "route_compare"),
            False,
            ("mixed_itinerary_request",),
            True,
        ),
        ("clarification_only", (), False, ("insufficient_capability_signal",), False),
    ],
)
def test_model_package_mapping_preserves_reason_codes_and_date_semantics(
    package_id: str,
    tools: tuple[str, ...],
    request_has_relative_date: bool,
    expected_reason_codes: tuple[str, ...],
    expected_include_date: bool,
) -> None:
    result = _parse_model_route(
        _completion_response(package_id, list(tools)),
        False,
        ALL_TOOLS,
        include_current_date=request_has_relative_date,
    )

    assert result is not None
    assert result.reason_codes == expected_reason_codes
    assert result.include_current_date is expected_include_date
