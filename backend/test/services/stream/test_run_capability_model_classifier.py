from __future__ import annotations

import json
import threading
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import Mock, patch

import litellm
import pytest
from pydantic import ValidationError

from app.db.models import Message
from app.services.stream.agent_task_policy import AgentTaskPolicy
from app.services.stream.run_capability_model_classifier import (
    _HARD_MAX_INPUT_TOKENS,
    _build_messages,
    _effective_classifier_limits,
    _error_type,
    _most_recent_complete_turn,
    _parse_model_route,
    _token_counter_model,
    classify_capability_request_with_model,
)
from app.services.stream.run_capability_router import resolve_run_capability_route
from app.utils.run_capability_contract import McpRouteTool

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


def _completion_response(
    package_id: str,
    explicit_tool_names: list[str] | None = None,
    *,
    network_policy: str = "allow",
    denied_tool_names: list[str] | None = None,
    required_primary_tool_name: str | None = None,
) -> SimpleNamespace:
    if required_primary_tool_name is None and package_id in {"mobility_intercity", "mixed_itinerary"}:
        required_primary_tool_name = (explicit_tool_names or ["route_compare"])[0]
    payload = {
        "package_id": package_id,
        "explicit_tool_names": explicit_tool_names or [],
        "network_policy": network_policy,
        "denied_tool_names": denied_tool_names or [],
    }
    if required_primary_tool_name is not None:
        payload["required_primary_tool_name"] = required_primary_tool_name
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


def _assert_unavailable_fallback(candidate) -> None:
    # 分类失败统一落到可选联网兜底，由回答模型自行判断是否需要检索。
    assert candidate.package_id == "fresh_web"
    assert candidate.confidence == "low"
    assert candidate.reason_codes == ("classifier_unavailable",)
    assert candidate.explicit_tool_names is None
    # 分类失败也要带今天日期，否则模型会拿训练截止时间当“今天”作答。
    assert candidate.include_current_date is True


def test_removed_transform_literal_delegates_to_model() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("transform"),
    ) as completion:
        candidate = classify_capability_request_with_model(
            "把 See you tomorrow 翻译成中文",
            ALL_TOOLS,
        )

    assert candidate.package_id == "transform"
    completion.assert_called_once()


def test_model_call_is_single_bounded_and_maps_weather() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("weather", ["weather_forecast"]),
    ) as completion:
        candidate = classify_capability_request_with_model("周末上海适合出门吗？", ALL_TOOLS)

    assert candidate.package_id == "weather"
    assert candidate.explicit_tool_names == ("weather_forecast",)
    assert candidate.include_current_date is True
    assert candidate.network_policy == "allow"
    assert candidate.denied_tool_names == ()
    completion.assert_called_once()
    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == "litellm_proxy/deepseek-chat"
    assert kwargs["timeout"] == 1.5
    assert kwargs["num_retries"] == 0
    assert kwargs["max_tokens"] == 128
    assert kwargs["temperature"] == 0
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["api_base"]
    assert kwargs["extra_body"]["metadata"]["tags"] == ["app:fusion", "phase:run_capability_classifier"]


def test_model_output_carries_tool_denials_without_another_call() -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response(
            "weather",
            ["weather_forecast"],
            network_policy="no_web_search",
            denied_tool_names=["url_read"],
        ),
    ) as completion:
        candidate = classify_capability_request_with_model("查上海天气，但别搜索也别打开网页", ALL_TOOLS)

    assert candidate.package_id == "weather"
    assert candidate.network_policy == "no_web_search"
    assert candidate.denied_tool_names == ("url_read",)
    completion.assert_called_once()


@pytest.mark.parametrize("denied", [["mcp_unrelated_tool"], ["tool_search"], ["weather_forecast", "weather_forecast"]])
def test_invalid_model_denial_fails_closed(denied: list[str]) -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("weather", ["weather_forecast"], denied_tool_names=denied),
    ):
        candidate = classify_capability_request_with_model("查上海天气", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)


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
    assert candidate.required_primary_tool_name == "search_flights"
    assert candidate.include_current_date is True


@pytest.mark.parametrize(
    ("primary", "denied"),
    [(None, []), ("web_search", [])],
)
def test_multi_product_route_requires_usable_primary_tool(primary: str | None, denied: list[str]) -> None:
    payload = {
        "package_id": "mobility_intercity",
        "explicit_tool_names": ["route_compare", "search_flights", "search_trains"],
        "network_policy": "allow",
        "denied_tool_names": denied,
    }
    if primary is not None:
        payload["required_primary_tool_name"] = primary
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    assert _parse_model_route(response, ALL_TOOLS, include_current_date=True) is None


def test_model_can_select_exact_authorized_mcp_alias() -> None:
    alias = "mcp_notion_search"
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("mcp_explicit", [alias]),
    ) as completion:
        candidate = classify_capability_request_with_model("这次请用 mcp_notion_search。", [*ALL_TOOLS, alias])

    assert candidate.package_id == "mcp_explicit"
    assert candidate.explicit_tool_names == (alias,)
    assert candidate.reason_codes == ("explicit_authorized_tool_alias",)
    assert candidate.include_current_date is True
    assert alias in completion.call_args.kwargs["messages"][0]["content"]


@pytest.mark.parametrize(
    "tool_names",
    [[], ["mcp_other_search"], ["web_search", "url_read"]],
)
def test_model_mcp_alias_not_authorized_fails_closed(tool_names: list[str]) -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("mcp_explicit", ["mcp_notion_search"]),
    ):
        candidate = classify_capability_request_with_model("这次请用 mcp_notion_search。", tool_names)

    _assert_unavailable_fallback(candidate)


@pytest.mark.parametrize("names", [[], ["mcp_notion_search", "mcp_other_search"]])
def test_model_mcp_alias_requires_exactly_one_name(names: list[str]) -> None:
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("mcp_explicit", names),
    ):
        candidate = classify_capability_request_with_model(
            "这次请用 mcp_notion_search。", [*ALL_TOOLS, "mcp_notion_search", "mcp_other_search"]
        )

    _assert_unavailable_fallback(candidate)


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


def test_context_is_dropped_before_current_message_when_input_budget_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()


def test_missing_credentials_fails_closed_without_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY", "")
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [TimeoutError("deadline"), RuntimeError("proxy unavailable")],
    ids=["timeout", "exception"],
)
def test_call_failures_switch_to_fallback_model_once(error) -> None:
    # 调用本身失败不是输出问题，回传错误也修不好；换备用模型再试一次。
    completion = Mock(side_effect=[error, _completion_response("direct")])
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    assert candidate.package_id == "direct"
    assert completion.call_count == 2
    primary, fallback = (call.kwargs for call in completion.call_args_list)
    assert primary["model"] == "litellm_proxy/deepseek-chat"
    assert fallback["model"] == "litellm_proxy/qwen3.8-flash"
    assert fallback["messages"] == primary["messages"]
    # 千问只认 enable_thinking，带 reasoning_effort 会被代理 400。
    assert "reasoning_effort" not in fallback
    assert fallback["extra_body"]["enable_thinking"] is False
    assert fallback["extra_body"]["cache"] == primary["extra_body"]["cache"]
    assert fallback["timeout"] <= primary["timeout"]


@pytest.mark.parametrize(
    "error",
    [TimeoutError("deadline"), RuntimeError("proxy unavailable")],
    ids=["timeout", "exception"],
)
def test_fallback_model_failure_falls_back_to_unavailable_route(error) -> None:
    completion = Mock(side_effect=error)
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    assert completion.call_count == 2


@pytest.mark.parametrize("fallback_model", ["", "deepseek-chat", "litellm_proxy/qwen3.8-flash"])
def test_invalid_or_same_fallback_model_is_not_called(monkeypatch: pytest.MonkeyPatch, fallback_model: str) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_FALLBACK_MODEL",
        fallback_model,
    )
    completion = Mock(side_effect=RuntimeError("proxy unavailable"))
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    completion.assert_called_once()


def test_fallback_model_is_skipped_when_remaining_budget_is_too_small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.CLASSIFIER_TOTAL_DEADLINE_SECONDS",
        0.1,
    )
    completion = Mock(side_effect=RuntimeError("proxy unavailable"))
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    completion.assert_called_once()


_MALFORMED = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])


@pytest.mark.parametrize(
    "invalid_response",
    [
        _completion_response("unknown_package"),
        _completion_response("weather", ["web_search"]),
        _MALFORMED,
    ],
    ids=["unknown-package", "illegal-tool-combination", "malformed-json"],
)
def test_invalid_output_is_repaired_once_then_fails_closed(invalid_response) -> None:
    completion = Mock(return_value=invalid_response)
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    assert completion.call_count == 2


def test_invalid_output_repair_feeds_back_structured_error_and_uses_corrected_route() -> None:
    invalid = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"package_id": "fresh_web", "network_policy": "maybe"}'))
        ]
    )
    completion = Mock(side_effect=[invalid, _completion_response("fresh_web", ["web_search"])])
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("特朗普为啥宣布全美降半旗一周？", ALL_TOOLS)

    assert candidate.package_id == "fresh_web"
    assert candidate.explicit_tool_names == ("web_search",)
    first_messages = completion.call_args_list[0].kwargs["messages"]
    retry_messages = completion.call_args_list[1].kwargs["messages"]
    assert retry_messages[: len(first_messages)] == first_messages
    assert retry_messages[-2] == {"role": "assistant", "content": invalid.choices[0].message.content}
    feedback = retry_messages[-1]
    assert feedback["role"] == "user"
    # 回传的是具体字段级错误，而不是笼统的“格式错误”。
    assert "network_policy" in feedback["content"]
    assert "explicit_tool_names" in feedback["content"]


def test_repair_is_skipped_when_remaining_budget_is_too_small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.CLASSIFIER_TOTAL_DEADLINE_SECONDS",
        0.1,
    )
    completion = Mock(return_value=_MALFORMED)
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS)

    _assert_unavailable_fallback(candidate)
    completion.assert_called_once()


def test_echoed_response_format_field_does_not_invalidate_route() -> None:
    # 实测 deepseek-chat 约 1/4 概率把 response_format 抄进输出，路由字段本身完全正确。
    echoed = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"type":"json_object","package_id":"fresh_web","explicit_tool_names":["web_search"],'
                        '"network_policy":"allow","denied_tool_names":[]}'
                    )
                )
            )
        ]
    )
    completion = Mock(return_value=echoed)
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion", completion):
        candidate = classify_capability_request_with_model("特朗普为啥宣布全美降半旗一周？", ALL_TOOLS)

    assert candidate.package_id == "fresh_web"
    completion.assert_called_once()


def test_result_callback_observes_delegated_model_and_fail_closed_results() -> None:
    events = []

    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        side_effect=[_completion_response("transform"), _completion_response("direct")],
    ):
        transform = classify_capability_request_with_model(
            "把 See you tomorrow 翻译成中文",
            ALL_TOOLS,
            result_callback=lambda result, error_type: events.append((result, error_type)),
        )
        model = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            result_callback=lambda result, error_type: events.append((result, error_type)),
        )
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        side_effect=TimeoutError("deadline"),
    ):
        failed = classify_capability_request_with_model(
            "另一个需要语义判断的请求",
            ALL_TOOLS,
            result_callback=lambda result, error_type: events.append((result, error_type)),
        )

    assert transform.package_id == "transform"
    assert model.package_id == "direct"
    _assert_unavailable_fallback(failed)
    assert events == [("model", None), ("model", None), ("failed", "timeout")]


def test_deadline_signal_turns_late_model_result_into_failed_observation() -> None:
    """外层 deadline 已结束时，迟到 response 不得再记录 model 成功。"""

    deadline_event = threading.Event()
    observations = []

    def _late_completion(**_kwargs):
        deadline_event.set()
        return _completion_response("weather", ["weather_forecast"])

    with (
        patch(
            "app.services.stream.run_capability_model_classifier.litellm.completion",
            side_effect=_late_completion,
        ) as completion,
        patch("app.services.stream.run_capability_model_classifier.logger.info") as log_info,
    ):
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            deadline_event=deadline_event,
            result_callback=lambda result, error_type: observations.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_called_once()
    assert observations == [("failed", "deadline_exceeded")]
    assert [call.args[1] for call in log_info.call_args_list] == ["failed"]


def test_deadline_signal_set_by_token_counter_skips_completion_once() -> None:
    """构建消息期间到期后不得新发模型调用，且只观测一次 deadline 失败。"""

    deadline_event = threading.Event()
    observations = []

    def _token_counter(**_kwargs) -> int:
        deadline_event.set()
        return 1

    with (
        patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion,
        patch("app.services.stream.run_capability_model_classifier.logger.info") as log_info,
    ):
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            deadline_event=deadline_event,
            token_counter_fn=_token_counter,
            result_callback=lambda result, error_type: observations.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()
    assert observations == [("failed", "deadline_exceeded")]
    assert [call.args[1] for call in log_info.call_args_list] == ["failed"]


@pytest.mark.parametrize(
    "response_or_error",
    [
        _completion_response("weather", ["weather_forecast"]),
        RuntimeError("late proxy error"),
    ],
    ids=["late-response", "late-error"],
)
def test_deadline_worker_discards_late_completion_observation(response_or_error) -> None:
    """Runner worker 已被 deadline 放弃后，正常或异常 completion 都不重复观测。"""

    deadline_event = threading.Event()
    observations = []

    def _late_completion(**_kwargs):
        deadline_event.set()
        if isinstance(response_or_error, BaseException):
            raise response_or_error
        return response_or_error

    with (
        patch(
            "app.services.stream.run_capability_model_classifier.litellm.completion",
            side_effect=_late_completion,
        ) as completion,
        patch("app.services.stream.run_capability_model_classifier.logger.info") as log_info,
    ):
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            deadline_event=deadline_event,
            suppress_deadline_observation=True,
            result_callback=lambda result, error_type: observations.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_called_once()
    assert observations == []
    log_info.assert_not_called()


def test_deadline_gate_prevents_completion_when_merge_kwargs_expires() -> None:
    """调用参数求值期间 deadline 赢得原子 gate 时，不得再开始 completion。"""

    from app.services.stream.run_capability_model_classifier import ClassifierDeadlineGate

    gate = ClassifierDeadlineGate()

    def _merge_kwargs(*_args, **_kwargs):
        gate.expire()
        return {}

    with (
        patch("app.services.stream.run_capability_model_classifier.merge_litellm_kwargs", _merge_kwargs),
        patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion,
    ):
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            deadline_gate=gate,
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()


def test_deadline_gate_prevents_second_history_token_counter_after_first_counter_expires() -> None:
    """首轮计数跨 deadline 后，不得启动裁剪历史的第二次计数。"""

    from app.services.stream.run_capability_model_classifier import ClassifierDeadlineGate

    gate = ClassifierDeadlineGate()
    counter_gate_states = []
    observations = []

    def _token_counter(**_kwargs) -> int:
        counter_gate_states.append(gate.is_expired())
        if len(counter_gate_states) == 1:
            gate.expire_and_publish_deadline()
            return 2001
        return 1

    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            conversation_messages=[
                {"role": "user", "content": "历史用户消息"},
                {"role": "assistant", "content": "历史助手回复"},
            ],
            token_counter_fn=_token_counter,
            deadline_gate=gate,
            result_callback=lambda result, error_type: observations.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    assert counter_gate_states == [False]
    assert observations == [("failed", "deadline_exceeded")]
    completion.assert_not_called()


def test_deadline_gate_logs_elapsed_duration_from_gate_creation() -> None:
    """outer deadline 的日志耗时必须覆盖 gate 已等待的单调时间。"""

    from app.services.stream.run_capability_model_classifier import ClassifierDeadlineGate

    clock_values = iter([10.0, 10.025])
    gate = ClassifierDeadlineGate(clock=lambda: next(clock_values))

    with patch("app.services.stream.run_capability_model_classifier.logger.info") as log_info:
        gate.expire_and_publish_deadline()

    assert log_info.call_args.args[3] == 25


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
    ["中" * 40000, "word " * 40000, "🚀" * 40000],
    ids=["中文", "英文", "emoji"],
)
def test_real_token_counter_truncates_long_chinese_english_and_emoji(message: str) -> None:
    # 超预算不再直接失败：保留开头和结尾送分类，回答模型仍拿到完整原文。
    message = "开头要求" + message + "结尾要求"
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("direct"),
    ) as completion:
        candidate = classify_capability_request_with_model(
            message,
            ALL_TOOLS,
            token_counter_fn=litellm.token_counter,
        )

    assert candidate.package_id == "direct"
    sent = completion.call_args.kwargs["messages"]
    user_content = sent[-1]["content"]
    assert user_content.startswith("开头要求")
    assert user_content.endswith("结尾要求")
    assert "中间内容过长，已省略" in user_content
    assert litellm.token_counter(model=_token_counter_model(), messages=sent) <= _HARD_MAX_INPUT_TOKENS


def test_token_counter_uses_classifier_model_family() -> None:
    token_counter = Mock(return_value=2001)
    classify_capability_request_with_model("需要语义判断的请求", ALL_TOOLS, token_counter_fn=token_counter)

    assert token_counter.call_args.kwargs["model"] == "deepseek/deepseek-chat"


def test_token_counter_failure_fails_closed_without_model_call() -> None:
    results: list[tuple[str, str | None]] = []
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            token_counter_fn=Mock(side_effect=RuntimeError("tokenizer unavailable")),
            result_callback=lambda result, error_type: results.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()
    assert results == [("failed", "token_count_failed")]


def test_over_budget_reports_input_budget_exceeded() -> None:
    results: list[tuple[str, str | None]] = []
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            # 恒定超预算：连系统提示都放不下，截断也救不回来。
            token_counter_fn=Mock(return_value=_HARD_MAX_INPUT_TOKENS + 1),
            result_callback=lambda result, error_type: results.append((result, error_type)),
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()
    assert results == [("failed", "input_budget_exceeded")]


def test_token_budget_drops_history_before_rejecting_current_message() -> None:
    def token_counter(*, messages, **_kwargs) -> int:
        return _HARD_MAX_INPUT_TOKENS + 1 if any("旧轮次" in item["content"] for item in messages) else 20

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


def test_route_result_only_history_anchors_adjacent_comparison_without_raw_result_dump() -> None:
    history = [
        {"role": "user", "content": [{"type": "text", "text": "从北京站到故宫怎么走"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "route_results",
                    "origin": {"label": "北京站", "city": "北京"},
                    "destination": {"label": "故宫", "city": "北京"},
                    "routes": [
                        {"mode": "transit", "duration_s": 1800, "transfers": 1, "summary": "不要送入分类器"},
                        {"mode": "driving", "duration_s": 2400},
                    ],
                    "provider": "amap",
                    "tool_call_log_id": "private-log-id",
                }
            ],
        },
        {"role": "user", "content": [{"type": "text", "text": "哪个更合适？"}]},
    ]

    turn = _most_recent_complete_turn(history)

    assert len(turn) == 2
    assert turn[0]["content"] == "从北京站到故宫怎么走"
    assert '"origin":"北京站（北京）"' in turn[1]["content"]
    assert '"destination":"故宫（北京）"' in turn[1]["content"]
    assert '"mode":"transit"' in turn[1]["content"]
    assert "private-log-id" not in turn[1]["content"]
    assert "不要送入分类器" not in turn[1]["content"]

    messages = _build_messages("哪个更合适？", ALL_TOOLS, history, token_counter_fn=lambda **_: 1)
    assert messages is not None
    assert messages[1:3] == turn


def test_system_prompt_defines_taxonomy_tool_mapping_order_and_negative_boundaries() -> None:
    messages = _build_messages(
        "当前消息",
        ALL_TOOLS,
        None,
        token_counter_fn=lambda **_kwargs: 1,
    )

    assert messages is not None
    prompt = messages[0]["content"]
    assert "fresh_web: latest or current external facts" in prompt
    assert "measured values that vary over time" in prompt
    assert "such as air quality" not in prompt
    assert all(
        term not in prompt.lower() for term in ("紫外线", "花粉", "水质", "ultraviolet", "pollen", "water quality")
    )
    assert "verified_web: requests verification or official/reliable sources" in prompt
    assert "url_read: reads or summarizes a supplied URL" in prompt
    assert "mobility_route: explicitly asks for a local route" in prompt
    assert "mobility_intercity: supplies intercity origin and destination" in prompt
    assert "travel_air_rail: compares flights and trains only" in prompt
    assert "mixed_itinerary: combines exactly 2 to 3 indispensable product tools" in prompt
    assert "prior_route_results summary" in prompt
    assert "canonical order" in prompt
    assert "Never choose deep_research" in prompt
    assert "Trusted global tool-disable settings are enforced by the server" in prompt
    assert "network_policy and denied_tool_names" in prompt
    assert "standard package must represent the capability the request actually needs" in prompt
    assert "mcp_explicit: an authorized MCP tool listed below is the best fit" in prompt
    assert "The user does not need to mention the service or the alias" in prompt
    assert "prefer mcp_explicit over direct, fresh_web, and verified_web" in prompt
    assert "Usage versus concept" in prompt
    assert "capitalization and phrasing never change this decision" in prompt
    assert "Choose direct for conceptual or general explanations" in prompt
    assert "Product packages (weather, place_discovery" in prompt
    assert "The authorized MCP list appended below contains exact aliases" in prompt
    assert "Authorized MCP tools for this request: []" in prompt
    assert "organizations, careers, products, or funding stages" in prompt
    assert "Do not choose fresh_web merely because a stage name appears" in prompt
    assert "never make a request compound" in prompt
    assert "classify by that task alone as if they were absent" in prompt
    assert "route capability requires both a locatable origin and destination" in prompt
    assert "only a destination is provided" in prompt


def test_classifier_prompt_lists_only_structurally_valid_authorized_mcp_aliases() -> None:
    messages = _build_messages(
        "这次请用 mcp_notion_search。",
        ["web_search", "mcp_notion_search", "mcp_notion_search", "mcp_invalid.name"],
        None,
        token_counter_fn=lambda **_kwargs: 1,
    )

    assert messages is not None
    assert 'Authorized MCP tools for this request: [{"alias":"mcp_notion_search"}]' in messages[0]["content"]
    assert "mcp_invalid.name" not in messages[0]["content"]


def test_classifier_prompt_labels_callable_mcp_tools_with_admin_configured_names() -> None:
    messages = _build_messages(
        "Azure Functions 的默认超时是多少？",
        ["web_search", "mcp_learn_search", "mcp_unlabeled"],
        None,
        mcp_tool_catalog=(McpRouteTool("mcp_learn_search", "server-learn", "Microsoft Learn 官方文档 / docs_search"),),
        token_counter_fn=lambda **_kwargs: 1,
    )

    assert messages is not None
    assert (
        'Authorized MCP tools for this request: [{"alias":"mcp_learn_search",'
        '"service_tool":"Microsoft Learn 官方文档 / docs_search"},{"alias":"mcp_unlabeled"}]'
    ) in messages[0]["content"]


def test_model_selecting_one_mcp_tool_announces_same_service_tools_only() -> None:
    catalog = (
        McpRouteTool("mcp_c7_resolve", "server-c7", "Context7 / resolve-library-id"),
        McpRouteTool("mcp_c7_query", "server-c7", "Context7 / query-docs"),
        McpRouteTool("mcp_learn_search", "server-learn", "Microsoft Learn / docs_search"),
    )
    tools = [*ALL_TOOLS, "mcp_c7_resolve", "mcp_c7_query", "mcp_learn_search"]
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("mcp_explicit", ["mcp_c7_query"]),
    ):
        candidate = classify_capability_request_with_model(
            "FastAPI 的依赖注入怎么写？", tools, mcp_tool_catalog=catalog
        )

    assert candidate.package_id == "mcp_explicit"
    assert candidate.explicit_tool_names == ("mcp_c7_query", "mcp_c7_resolve")


def test_missing_explicit_tool_names_is_rejected() -> None:
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"package_id":"direct"}'))])

    with pytest.raises(ValidationError):
        _parse_model_route(response, ALL_TOOLS, include_current_date=False)


def test_missing_constraint_fields_are_rejected() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"package_id":"direct","explicit_tool_names":[]}'))]
    )

    with pytest.raises(ValidationError):
        _parse_model_route(response, ALL_TOOLS, include_current_date=False)


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
        ("direct", (), False, ("stable_knowledge_question",), True),
        ("transform", (), False, ("text_transform_request",), True),
        ("date", (), False, ("current_date_question",), True),
        ("fresh_web", ("web_search",), False, ("fresh_external_fact",), True),
        ("verified_web", ("web_search", "url_read"), False, ("verified_source_request",), True),
        ("url_read", ("url_read",), False, ("explicit_url_read",), True),
        ("weather", ("weather_forecast",), False, ("explicit_weather_request",), True),
        ("place_discovery", ("local_place_search",), False, ("explicit_place_discovery",), True),
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
        ("clarification_only", (), False, ("insufficient_capability_signal",), True),
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
        ALL_TOOLS,
        include_current_date=request_has_relative_date,
    )

    assert result is not None
    assert result.reason_codes == expected_reason_codes
    assert result.include_current_date is expected_include_date


def test_high_classifier_configuration_is_clamped_at_final_call_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_TIMEOUT_SECONDS",
        9,
    )
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_MAX_OUTPUT_TOKENS",
        999,
    )
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("direct"),
    ) as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            token_counter_fn=lambda **_kwargs: 1,
        )

    assert candidate.package_id == "direct"
    assert completion.call_args.kwargs["timeout"] == 1.5
    assert completion.call_args.kwargs["max_tokens"] == 128


def test_high_input_and_context_configuration_is_clamped_to_hard_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS",
        90000,
    )
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_CONTEXT_TURNS",
        7,
    )

    limits = _effective_classifier_limits()
    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            token_counter_fn=lambda **_kwargs: 50000,
        )

    assert limits is not None
    assert limits.max_input_tokens == _HARD_MAX_INPUT_TOKENS
    assert limits.context_turns == 1
    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()


@pytest.mark.parametrize(
    "setting_name",
    [
        "RUN_CAPABILITY_CLASSIFIER_TIMEOUT_SECONDS",
        "RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS",
        "RUN_CAPABILITY_CLASSIFIER_MAX_OUTPUT_TOKENS",
        "RUN_CAPABILITY_CLASSIFIER_CONTEXT_TURNS",
    ],
)
def test_non_positive_classifier_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    setting_name: str,
) -> None:
    monkeypatch.setattr(f"app.services.stream.run_capability_model_classifier.settings.{setting_name}", 0)

    with patch("app.services.stream.run_capability_model_classifier.litellm.completion") as completion:
        candidate = classify_capability_request_with_model(
            "需要语义判断的请求",
            ALL_TOOLS,
            token_counter_fn=lambda **_kwargs: 1,
        )

    _assert_unavailable_fallback(candidate)
    completion.assert_not_called()


def test_token_counter_uses_explicit_tokenizer_model_for_non_deepseek_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_MODEL",
        "qwen-plus",
    )
    monkeypatch.setattr(
        "app.services.stream.run_capability_model_classifier.settings.RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL",
        "dashscope/qwen-plus",
        raising=False,
    )

    assert _token_counter_model() == "dashscope/qwen-plus"


def test_litellm_timeout_is_reported_as_timeout() -> None:
    timeout = litellm.Timeout("deadline", "deepseek-chat", "deepseek")

    assert _error_type(timeout) == "timeout"


def test_standard_package_is_not_rejected_when_runtime_tool_definitions_are_empty() -> None:
    response = _completion_response("weather", ["weather_forecast"])

    route = _parse_model_route(response, [], include_current_date=True)

    assert route is not None
    assert route.package_id == "weather"
    assert route.explicit_tool_names == ("weather_forecast",)


@pytest.mark.parametrize(
    ("tools_disabled", "knowledge_grounded", "capabilities", "expected_package", "expected_reason"),
    [
        (True, False, {"functionCalling": True, "searchCapable": True}, "tools_unavailable", "tools_disabled"),
        (
            False,
            False,
            {"functionCalling": False, "searchCapable": True},
            "tools_unavailable",
            "function_calling_unavailable",
        ),
        (True, True, {"functionCalling": True, "searchCapable": True}, "knowledge_grounded", "knowledge_grounded_mode"),
    ],
    ids=["disable-tools", "no-function-calling", "knowledge-grounded"],
)
def test_model_product_capability_is_degraded_by_existing_resolver_when_tools_are_unavailable(
    tools_disabled: bool,
    knowledge_grounded: bool,
    capabilities: dict[str, bool],
    expected_package: str,
    expected_reason: str,
) -> None:
    policy = AgentTaskPolicy(
        task_mode="standard",
        plan_mode="auto",
        network_profile="standard",
        evidence_policy="standard",
    )
    with patch(
        "app.services.stream.run_capability_model_classifier.litellm.completion",
        return_value=_completion_response("weather", ["weather_forecast"]),
    ):
        resolution = resolve_run_capability_route(
            original_message="明天上海天气怎样？",
            task_context_messages=None,
            available_tool_names=[],
            requested_plan_mode="auto",
            task_policy=policy,
            capabilities=capabilities,
            tools_disabled=tools_disabled,
            knowledge_grounded=knowledge_grounded,
            classify_fn=classify_capability_request_with_model,
        )

    assert resolution.package_id == expected_package
    assert resolution.reason_codes == (expected_reason,)
    assert resolution.include_current_date is True
    assert resolution.network_boundary_required is True


@pytest.mark.parametrize(
    ("package_id", "tools", "primary", "expected_package", "expected_tools", "expected_primary"),
    [
        # 兜底网页工具由服务端附加，模型列出时只剥离，不判为冲突。
        ("flight", ["web_search", "search_flights"], None, "flight", ("search_flights",), None),
        (
            "mixed_itinerary",
            ["web_search", "url_read", "local_place_search", "route_compare"],
            "local_place_search",
            "mixed_itinerary",
            ("local_place_search", "route_compare"),
            "local_place_search",
        ),
        # 剥离后只剩一个产品工具时，归到该工具的单产品包。
        ("mixed_itinerary", ["web_search", "search_flights"], "search_flights", "flight", ("search_flights",), None),
        (
            "mixed_itinerary",
            ["web_search", "url_read", "local_place_search"],
            "web_search",
            "place_discovery",
            ("local_place_search",),
            None,
        ),
    ],
)
def test_recovery_web_tools_listed_in_product_packages_are_stripped(
    package_id: str,
    tools: list[str],
    primary: str | None,
    expected_package: str,
    expected_tools: tuple[str, ...],
    expected_primary: str | None,
) -> None:
    result = _parse_model_route(
        _completion_response(package_id, tools, required_primary_tool_name=primary),
        ALL_TOOLS,
        include_current_date=False,
    )

    assert result is not None
    assert result.package_id == expected_package
    assert result.explicit_tool_names == expected_tools
    assert result.required_primary_tool_name == expected_primary


@pytest.mark.parametrize(
    ("package_id", "tools", "primary"),
    [
        # 网页包本身的工具不剥离；无外部工具的包列出网页工具仍是冲突。
        ("direct", ["web_search"], None),
        ("fresh_web", ["web_search", "url_read"], None),
        # 剥离后仍有多个产品工具却缺少主工具，不猜测主工具。
        ("mixed_itinerary", ["web_search", "local_place_search", "route_compare"], "web_search"),
    ],
)
def test_recovery_tool_normalization_keeps_other_mismatches_invalid(
    package_id: str, tools: list[str], primary: str | None
) -> None:
    result = _parse_model_route(
        _completion_response(package_id, tools, required_primary_tool_name=primary),
        ALL_TOOLS,
        include_current_date=False,
    )

    assert result is None
