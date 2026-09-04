"""触顶总结无证据事实边界（issue #30 P1-B）。"""

from __future__ import annotations

import pytest

from app.schemas.chat import TextBlock, ThinkingBlock
from app.services.stream.limit_summary_fact_guard import (
    NO_EVIDENCE_ANSWER_TEXT,
    has_tool_evidence,
    resolve_no_evidence_answer,
    unsupported_dynamic_fact_kind,
)

_FABRICATED_ANSWER = (
    "从武汉到桂林，高铁直达大约 5 小时，二等座 280 元左右；"
    "飞机航班 CZ3456 全程 1.5 小时，价格区间 600-900 元；自驾约 8 小时。"
)


class TestHasToolEvidence:
    def test_纯文本与思考块不算证据(self):
        blocks = [
            ThinkingBlock(type="thinking", id="t1", thinking="想一想"),
            TextBlock(type="text", id="a1", text="回答"),
        ]
        assert has_tool_evidence(blocks) is False

    def test_空块列表不算证据(self):
        assert has_tool_evidence([]) is False
        assert has_tool_evidence(None) is False

    @pytest.mark.parametrize(
        "block_type",
        ["search", "url_read", "knowledge_evidence", "route_results", "train_results", "weather_results"],
    )
    def test_工具证据块被识别(self, block_type: str):
        assert has_tool_evidence([{"type": block_type}]) is True


class TestUnsupportedDynamicFact:
    @pytest.mark.parametrize(
        ("answer", "kind"),
        [
            ("二等座 280 元左右", "price"),
            ("票价 ¥300", "price"),
            ("高铁直达大约 5 小时", "duration"),
            ("车程 50 分钟", "duration"),
            ("可以坐 G1234 次列车", "vehicle_number"),
            ("航班 CZ3456 上午出发", "vehicle_number"),
        ],
    )
    def test_具体动态数值被识别(self, answer: str, kind: str):
        assert unsupported_dynamic_fact_kind(answer) == kind

    @pytest.mark.parametrize(
        "answer",
        [
            "本次没能查到具体的班次和票价，建议稍后重试。",
            "国庆期间人比较多，建议错峰出行并提前规划。",
            "高铁和飞机都可以，具体班次要以购票平台为准。",
            "",
        ],
    )
    def test_不含具体数值的回答不触发(self, answer: str):
        assert unsupported_dynamic_fact_kind(answer) is None

    def test_用户自己说过的数值算上下文支撑(self):
        messages = [{"role": "user", "content": "我预算 300 元以内，路上别超过 5 小时"}]
        assert unsupported_dynamic_fact_kind("控制在 300 元、5 小时以内是可行的", messages=messages) is None

    def test_模型自己上一轮说的不算支撑(self):
        messages = [{"role": "assistant", "content": "大约 5 小时"}]
        assert unsupported_dynamic_fact_kind("大约 5 小时", messages=messages) == "duration"


class TestResolveNoEvidenceAnswer:
    def test_零工具证据时拦下伪造的班次价格时长(self):
        answer, kind = resolve_no_evidence_answer(_FABRICATED_ANSWER, content_blocks=[])
        assert kind is not None
        assert answer == NO_EVIDENCE_ANSWER_TEXT

    def test_有工具证据时一律放行(self):
        answer, kind = resolve_no_evidence_answer(
            _FABRICATED_ANSWER,
            content_blocks=[{"type": "train_results"}],
        )
        assert kind is None
        assert answer == _FABRICATED_ANSWER

    def test_零证据但回答诚实时放行(self):
        honest = "本次没能完成所需的查询，建议稍后重试。"
        answer, kind = resolve_no_evidence_answer(honest, content_blocks=[])
        assert kind is None
        assert answer == honest


class TestFactGuardBoundaries:
    """自查发现的两处边界：道路编号不是车次；天气数值同属无证据动态事实。"""

    @pytest.mark.parametrize(
        "answer",
        [
            "这条路线主要走 G4 高速",
            "从 G42 高速转 S1 省道",
            "沿 G107 国道往南",
        ],
    )
    def test_公路编号不被当成车次(self, answer: str):
        assert unsupported_dynamic_fact_kind(answer) is None

    @pytest.mark.parametrize(
        "answer",
        ["可以坐 G1234 次列车", "推荐 G4 次列车", "航班 MU5678 上午起飞"],
    )
    def test_车次航班号仍被识别(self, answer: str):
        assert unsupported_dynamic_fact_kind(answer) == "vehicle_number"

    @pytest.mark.parametrize(
        "answer",
        ["明天南京最高 18 度，最低 9 度", "白天气温 25℃", "夜里可能到 -2°C"],
    )
    def test_无证据的气温被识别(self, answer: str):
        assert unsupported_dynamic_fact_kind(answer) == "temperature"

    def test_用户自述的气温算上下文支撑(self):
        messages = [{"role": "user", "content": "我这边现在 18 度，需要穿外套吗"}]
        assert unsupported_dynamic_fact_kind("18 度确实该加件外套", messages=messages) is None
