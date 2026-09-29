from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.stream.routing_quality_observability import (
    ROUTING_QUALITY_SAMPLE_LIMIT,
    aggregate_routing_quality,
)

_WINDOW = {
    "created_from": datetime(2026, 9, 28, tzinfo=timezone.utc),
    "created_to": datetime(2026, 9, 29, tzinfo=timezone.utc),
}


def _start(run_id, resolution, *, minute=0):
    payload = {} if resolution is None else {"capability_resolution": resolution}
    return SimpleNamespace(
        run_id=run_id,
        conversation_id=f"conv-{run_id}",
        event_type="run_started",
        event_ts=datetime(2026, 9, 28, 12, minute),
        payload=payload,
    )


def _resolution(package_id, tools=(), *, reason_codes=("x",), primary=None):
    return {
        "package_id": package_id,
        "reason_codes": list(reason_codes),
        "external_tool_names": list(tools),
        "required_primary_tool_name": primary,
    }


def _tool(run_id, tool_name):
    return SimpleNamespace(run_id=run_id, payload={"tool_name": tool_name})


def _done(run_id, event_type="run_completed"):
    return SimpleNamespace(run_id=run_id, event_type=event_type)


def _aggregate(starts, tools=(), terminals=None):
    if terminals is None:
        terminals = [_done(item.run_id) for item in starts]
    return aggregate_routing_quality(
        {"starts": starts, "tool_events": list(tools), "terminal_events": terminals},
        **_WINDOW,
    )


def _signals_by_run(result):
    return {item["run_id"]: item["signals"] for item in result["samples"]}


def test_product_package_that_only_used_web_is_flagged_as_web_only_fallback():
    result = _aggregate(
        [_start("weather-web", _resolution("weather", ("web_search", "url_read", "weather_forecast")))],
        [_tool("weather-web", "web_search")],
    )

    assert result["summary"]["signals"]["web_only_fallback"] == 1
    assert result["summary"]["signals"]["no_tool_call"] == 0
    assert result["samples"][0]["called_tools"] == ["web_search"]


def test_product_tool_called_alongside_web_is_not_flagged():
    result = _aggregate(
        [_start("weather-ok", _resolution("weather", ("web_search", "weather_forecast")))],
        [_tool("weather-ok", "web_search"), _tool("weather-ok", "weather_forecast")],
    )

    assert result["samples"] == []
    assert result["by_package"][0]["signals"] == {name: 0 for name in result["summary"]["signals"]}


def test_package_with_announced_tools_but_no_calls_is_flagged_and_control_tools_do_not_count():
    result = _aggregate(
        [
            _start("fresh-none", _resolution("fresh_web", ("web_search",))),
            _start("direct", _resolution("direct")),
        ],
        [_tool("fresh-none", "update_plan")],
    )

    assert _signals_by_run(result) == {"fresh-none": ["no_tool_call"]}
    assert result["summary"]["total"] == 2


def test_required_primary_tool_missed_even_when_another_product_tool_ran():
    resolution = _resolution(
        "mixed_itinerary",
        ("web_search", "weather_forecast", "local_place_search"),
        primary="weather_forecast",
    )
    result = _aggregate([_start("mixed", resolution)], [_tool("mixed", "local_place_search")])

    assert _signals_by_run(result) == {"mixed": ["primary_tool_missed"]}
    assert result["samples"][0]["required_primary_tool_name"] == "weather_forecast"


def test_mcp_package_uses_announced_aliases_as_product_tools():
    resolution = _resolution("mcp_explicit", ("web_search", "url_read", "mcp_abc"))
    result = _aggregate(
        [_start("mcp-web", resolution), _start("mcp-ok", resolution)],
        [_tool("mcp-web", "web_search"), _tool("mcp-ok", "mcp_abc")],
    )

    assert _signals_by_run(result) == {"mcp-web": ["web_only_fallback"]}


def test_tool_signals_only_count_completed_runs_but_route_signals_count_all():
    starts = [
        _start("running", _resolution("fresh_web", ("web_search",), reason_codes=("classifier_unavailable",))),
        _start("interrupted", _resolution("weather", ("web_search", "weather_forecast"))),
        _start("clarify", _resolution("clarification_only")),
        _start("degraded", _resolution("tools_unavailable")),
    ]
    result = _aggregate(
        starts,
        terminals=[_done("interrupted", "run_interrupted"), _done("clarify"), _done("degraded", "run_failed")],
    )

    signals = result["summary"]["signals"]
    assert signals["classifier_unavailable"] == 1
    assert signals["clarification_only"] == 1
    assert signals["tools_unavailable"] == 1
    assert signals["no_tool_call"] == 0
    assert result["summary"]["completed"] == 1
    assert result["scope"]["running_count"] == 1
    assert result["scope"]["interrupted_count"] == 1
    assert result["scope"]["failed_count"] == 1
    # tools_unavailable 是能力可用性问题而非分类质量，不进可疑样本。
    assert _signals_by_run(result) == {"running": ["classifier_unavailable"], "clarify": ["clarification_only"]}


def test_unrouted_runs_are_counted_separately_and_packages_sorted_by_volume():
    starts = [
        _start("legacy", None),
        _start("d1", _resolution("direct")),
        _start("d2", _resolution("direct")),
        _start("w1", _resolution("fresh_web", ("web_search",))),
    ]
    result = _aggregate(starts, [_tool("w1", "web_search")])

    assert result["scope"]["unrouted_count"] == 1
    assert result["summary"]["total"] == 3
    assert [item["package_id"] for item in result["by_package"]] == ["direct", "fresh_web"]


def test_samples_are_newest_first_and_capped():
    starts = [
        _start(f"run-{index}", _resolution("clarification_only"), minute=index % 60)
        for index in range(ROUTING_QUALITY_SAMPLE_LIMIT + 5)
    ]
    result = _aggregate(starts)

    assert len(result["samples"]) == ROUTING_QUALITY_SAMPLE_LIMIT
    assert result["scope"]["sample_total"] == ROUTING_QUALITY_SAMPLE_LIMIT + 5
    started = [item["started_at"] for item in result["samples"]]
    assert started == sorted(started, reverse=True)
    assert started[0].endswith("+00:00")
