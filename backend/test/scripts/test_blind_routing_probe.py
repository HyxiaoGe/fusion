import hashlib
import pathlib
import re
import sys
from types import SimpleNamespace

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

from scripts import blind_routing_probe as probe  # noqa: E402


def test_default_mode_uses_hybrid_classifier(monkeypatch):
    observed_classifiers = []
    hybrid_classifier = object()

    def fake_route(message, *, classifier):
        observed_classifiers.append(classifier)
        return SimpleNamespace(package_id="direct", external_tool_names=())

    monkeypatch.setattr(probe, "classify_capability_request_with_model", hybrid_classifier, raising=False)
    monkeypatch.setattr(probe, "has_hybrid_classifier_credentials", lambda: True, raising=False)
    monkeypatch.setattr(probe, "route", fake_route)

    assert probe.main([]) == 0
    assert observed_classifiers == [hybrid_classifier] * 33


def test_rules_mode_reproduces_baseline_and_group_report(capsys):
    assert probe.main(["--classifier", "rules"]) == 0

    output = capsys.readouterr().out
    assert re.search(r"abstract\s+5\s+5\s+100%", output)
    assert re.search(r"合计\s+14\s+33\s+42%", output)


def test_default_mode_without_litellm_credentials_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(probe, "has_hybrid_classifier_credentials", lambda: False, raising=False)

    assert probe.main([]) != 0

    captured = capsys.readouterr()
    error = captured.err
    assert "LiteLLM" in error
    assert "LITELLM_PROXY_URL" in error
    assert "LITELLM_API_KEY" in error
    assert "不能报告准确率" in error
    assert "合计" not in captured.out


def test_blind_probe_fixture_is_unchanged():
    fixture_sha256 = hashlib.sha256(probe.FIXTURE.read_bytes()).hexdigest()

    assert fixture_sha256 == "dcadbc917207dc8dc96a7747d0114d3f67e6d0682dc3ec59a23244471e006c7e"
