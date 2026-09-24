"""#132 第一波固定授权否定输入，避免后续复测换样本。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.stream.dynamic_tool_discovery import resolve_discovery_network_denials
from app.services.stream.run_capability_request_signals import _extract_request_signals

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "issue132_network_denials_352.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_固定矩阵由前缀与动作笛卡尔积生成():
    assert len(_DATA["prefixes"]) == 16
    assert len(_DATA["actions"]) == 22
    assert [case["message"] for case in _DATA["cases"]] == [
        prefix + action for prefix in _DATA["prefixes"] for action in _DATA["actions"]
    ]


@pytest.mark.parametrize("case", _DATA["cases"], ids=lambda case: case["message"])
def test_旧路径与动态发现的授权否定均符合固定基线(case):
    request = _extract_request_signals(case["message"])
    legacy = (request.web_search_denied, request.url_read_denied, request.all_network_denied)
    discovery = resolve_discovery_network_denials(case["message"])

    assert legacy == tuple(case["legacy"])
    assert discovery == tuple(case["discovery"])
