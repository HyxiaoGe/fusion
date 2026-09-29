import json
import pathlib
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.stream.run_capability_router import _CandidateRoute  # noqa: E402
from scripts import blind_routing_probe as probe  # noqa: E402
from test.test_blind_routing_fixture import _ORIGINAL_CASE_IDS  # noqa: E402


@pytest.fixture(autouse=True)
def _valid_hybrid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe.settings, "LITELLM_API_KEY", "test-key")
    monkeypatch.setattr(probe.settings, "LITELLM_PROXY_URL", "http://litellm.test:4000")
    monkeypatch.setattr(probe.settings, "RUN_CAPABILITY_CLASSIFIER_MODEL", "deepseek-chat")


def _direct_candidate() -> _CandidateRoute:
    return _CandidateRoute(
        package_id="direct",
        confidence="high",
        reason_codes=("stable_knowledge_question",),
        include_current_date=True,
    )


def test_hybrid_monitor_uses_real_route_resolver_and_classifier_seam():
    calls = []

    def classifier(*, message, task_context_messages, available_tool_names, result_callback):
        calls.append((message, task_context_messages, available_tool_names))
        result_callback("model", None)
        return _direct_candidate()

    monitor = probe.HybridClassifierMonitor(classifier)
    resolution = probe.route("需要语义分类的请求", classifier=monitor)

    assert resolution.package_id == "direct"
    assert calls == [("需要语义分类的请求", None, probe.AVAILABLE_TOOLS)]
    assert monitor.failure_error_type is None


def test_rules_mode_reports_each_group_without_pinning_historical_score(monkeypatch, tmp_path, capsys):
    # 规则探针只检查报告结构；删除字面判据后不能沿用旧的 14/33 分数。
    payload = json.loads(probe.FIXTURE.read_text(encoding="utf-8"))
    payload["cases"] = [case for case in payload["cases"] if case["id"] in _ORIGINAL_CASE_IDS]
    fixture = tmp_path / "original_cases.json"
    fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(probe, "FIXTURE", fixture)

    assert probe.main(["--classifier", "rules"]) == 0

    output = capsys.readouterr().out
    assert re.search(r"abstract\s+\d+\s+5\s+\d+%", output)
    assert re.search(r"合计\s+\d+\s+33\s+\d+%", output)


@pytest.mark.parametrize(
    ("setting_name", "value"),
    [
        ("LITELLM_API_KEY", "  "),
        ("LITELLM_PROXY_URL", "not-a-url"),
        ("RUN_CAPABILITY_CLASSIFIER_MODEL", " deepseek-chat "),
        ("RUN_CAPABILITY_CLASSIFIER_MODEL", " litellm_proxy/deepseek-chat "),
    ],
    ids=["blank-api-key", "invalid-proxy-url", "padded-model-alias", "prefixed-model-alias"],
)
def test_default_mode_rejects_invalid_hybrid_settings(monkeypatch, capsys, setting_name, value):
    monkeypatch.setattr(probe.settings, setting_name, value)

    assert probe.main([]) == 2

    captured = capsys.readouterr()
    assert "LiteLLM" in captured.err
    assert "不能报告准确率" in captured.err
    assert "合计" not in captured.out
    assert "OK " not in captured.out
    assert "MISS" not in captured.out


@pytest.mark.parametrize(
    "completion_result",
    [
        TimeoutError("deadline"),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]),
    ],
    ids=["timeout", "invalid-output"],
)
def test_default_mode_runtime_classifier_failure_blocks_all_probe_output(monkeypatch, capsys, completion_result):
    from app.services.stream import run_capability_model_classifier as model_classifier

    monkeypatch.setattr(model_classifier.litellm, "token_counter", lambda **_kwargs: 1)
    completion = Mock(
        side_effect=completion_result if isinstance(completion_result, BaseException) else None,
        return_value=None if isinstance(completion_result, BaseException) else completion_result,
    )
    monkeypatch.setattr(model_classifier.litellm, "completion", completion)

    assert probe.main(["--verbose"]) == 3

    captured = capsys.readouterr()
    assert "模型分类失败" in captured.err
    assert "合计" not in captured.out
    assert "类别" not in captured.out
    assert "OK " not in captured.out
    assert "MISS" not in captured.out
    assert completion.called


@pytest.mark.parametrize("available_tools", [None, [], ["mcp_notion_search"]])
def test_case_tools_reach_real_classifier_seam(monkeypatch, tmp_path, available_tools):
    case = {"id": "工具覆盖", "question": "需要语义分类的请求", "group": "脚本测试", "acceptable_packages": ["direct"]}
    if available_tools is not None:
        case["available_tools"] = available_tools
    fixture = tmp_path / "case_tools.json"
    fixture.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(probe, "FIXTURE", fixture)
    classifier = Mock(return_value=_direct_candidate())
    monkeypatch.setattr(probe, "classify_capability_request", classifier)

    assert probe.main(["--classifier", "rules"]) == 0

    classifier.assert_called_once_with(
        message=case["question"],
        task_context_messages=None,
        available_tool_names=probe.AVAILABLE_TOOLS if available_tools is None else available_tools,
    )


def _write_cases(monkeypatch, tmp_path, cases, gate=None):
    payload = {"cases": cases}
    if gate is not None:
        payload["gate"] = gate
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(probe, "FIXTURE", fixture)


def _case(case_id, **extra):
    return {"id": case_id, "question": f"问题{case_id}", "group": "direct", "acceptable_packages": ["direct"], **extra}


def _monitor_returning(candidates_by_question, failures=()):
    """按问题返回候选；failures 里的问题回报分类失败。"""

    def classifier(*, message, task_context_messages, available_tool_names, result_callback):
        if message in failures:
            result_callback("failed", "timeout")
        else:
            result_callback("model", None)
        candidates = candidates_by_question[message]
        return candidates.pop(0) if isinstance(candidates, list) else candidates

    return lambda: probe.HybridClassifierMonitor(classifier)


def _candidate(package_id, reason_codes, tools=None, **extra):
    return _CandidateRoute(package_id, "high", reason_codes, True, explicit_tool_names=tools, **extra)


_GATE = {"min_pass_rate": 1.0, "min_consistency": 1.0, "max_classifier_failure_rate": 0.5, "repeat": 2}


def test_gate_passes_when_every_repeat_lands_in_acceptable_packages(monkeypatch, tmp_path, capsys):
    _write_cases(monkeypatch, tmp_path, [_case("a"), _case("b")], _GATE)
    monkeypatch.setattr(
        probe,
        "_new_hybrid_classifier_monitor",
        _monitor_returning({"问题a": _direct_candidate(), "问题b": _direct_candidate()}),
    )

    assert probe.main(["--gate"]) == 0
    assert "门禁通过（每条 2 次）" in capsys.readouterr().out


def test_gate_fails_on_inconsistent_repeats_and_reports_where_misses_went(monkeypatch, tmp_path, capsys):
    web = _candidate("fresh_web", ("fresh_external_fact",), ("web_search",))
    _write_cases(monkeypatch, tmp_path, [_case("a")], _GATE)
    monkeypatch.setattr(
        probe, "_new_hybrid_classifier_monitor", _monitor_returning({"问题a": [_direct_candidate(), web]})
    )

    assert probe.main(["--gate"]) == 1
    captured = capsys.readouterr()
    assert "实际=direct,fresh_web" in captured.out
    assert "误判去向：fresh_web×1" in captured.out
    assert "通过率 0.0% 低于门槛" in captured.err
    assert "一致率 0.0% 低于门槛" in captured.err


def test_gate_requires_critical_cases_even_when_rate_threshold_is_met(monkeypatch, tmp_path, capsys):
    web = _candidate("fresh_web", ("fresh_external_fact",), ("web_search",))
    gate = {**_GATE, "min_pass_rate": 0.5, "min_consistency": 0.5}
    _write_cases(monkeypatch, tmp_path, [_case("a"), _case("b", critical=True)], gate)
    monkeypatch.setattr(
        probe, "_new_hybrid_classifier_monitor", _monitor_returning({"问题a": _direct_candidate(), "问题b": web})
    )

    assert probe.main(["--gate"]) == 1
    assert "关键条目未通过：b" in capsys.readouterr().err


def test_forbidden_and_required_tools_are_checked_beyond_package(monkeypatch, tmp_path, capsys):
    weather = _candidate("weather", ("explicit_weather_request",), ("weather_forecast",))
    cases = [
        _case("a", acceptable_packages=["weather"], forbidden_tools=["web_search"]),
        _case("b", acceptable_packages=["weather"], required_tools=["search_trains"]),
    ]
    _write_cases(monkeypatch, tmp_path, cases)
    monkeypatch.setattr(
        probe, "_new_hybrid_classifier_monitor", _monitor_returning({"问题a": weather, "问题b": weather})
    )

    assert probe.main([]) == 0
    output = capsys.readouterr().out
    # weather 包自动附带网页兜底工具，用户禁止联网时这应当被判为未通过。
    assert "不应开放=['web_search']" in output
    assert "缺少工具=['search_trains']" in output
    assert re.search(r"合计\s+0\s+2", output)


def test_context_turn_reaches_classifier(monkeypatch, tmp_path):
    seen = []

    def classifier(*, message, task_context_messages, available_tool_names, result_callback):
        seen.append(task_context_messages)
        result_callback("model", None)
        return _direct_candidate()

    context = [{"role": "user", "content": "上一问"}, {"role": "assistant", "content": "上一答"}]
    _write_cases(monkeypatch, tmp_path, [_case("a", context=context)])
    monkeypatch.setattr(probe, "_new_hybrid_classifier_monitor", lambda: probe.HybridClassifierMonitor(classifier))

    assert probe.main([]) == 0
    assert seen == [context]


def test_sparse_classifier_failures_are_counted_not_fatal(monkeypatch, tmp_path, capsys):
    _write_cases(monkeypatch, tmp_path, [_case("a"), _case("b"), _case("c")])
    monkeypatch.setattr(
        probe,
        "_new_hybrid_classifier_monitor",
        _monitor_returning(
            {"问题a": _direct_candidate(), "问题b": _direct_candidate(), "问题c": _direct_candidate()}, {"问题c"}
        ),
    )
    monkeypatch.setattr(probe, "_DEFAULT_GATE", {**probe._DEFAULT_GATE, "max_classifier_failure_rate": 0.5})

    assert probe.main([]) == 0
    output = capsys.readouterr().out
    assert "分类失败：timeout×1" in output
    assert re.search(r"合计\s+2\s+3", output)
