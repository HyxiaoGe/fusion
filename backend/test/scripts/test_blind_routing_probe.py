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
        include_current_date=False,
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
    completion.assert_called_once()


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
