"""触顶总结无证据事实边界（issue #30 P1-B）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.chat import SearchSource, TextBlock, ThinkingBlock
from app.services.external.reader_client import UrlReadResponse, UrlReadResult
from app.services.stream.limit_summary_fact_guard import (
    NO_EVIDENCE_ANSWER_TEXT,
    has_tool_evidence,
    resolve_no_evidence_answer,
)
from app.services.stream.run_capability_router import RunCapabilityResolution
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence
from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import UrlReadHandler
from app.services.tool_handlers.web_search import WebSearchHandler

_FABRICATED_ANSWER = (
    "从武汉到桂林，高铁直达大约 5 小时，二等座 280 元左右；"
    "飞机航班 CZ3456 全程 1.5 小时，价格区间 600-900 元；自驾约 8 小时。"
)


def _capability(package_id: str) -> RunCapabilityResolution:
    return RunCapabilityResolution(
        schema_version=1,
        router_version="test",
        package_id=package_id,
        confidence="high",
        resolution_mode="routed",
        reason_codes=(),
        external_tool_names=(),
        effective_plan_mode="off",
        include_current_date=False,
        network_boundary_required=False,
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
        "block",
        [
            {"type": "search", "status": "failed", "sources": []},
            {"type": "search", "status": "failed", "sources": [{"url": "https://example.com"}]},
            {"type": "search", "status": "success", "sources": []},
            {"type": "search", "status": "success", "sources": [{"url": "https://example.com"}]},
            {"type": "search", "status": "degraded", "source_count": 1, "source_refs": []},
            {"type": "url_read", "status": "degraded", "url": "https://example.com"},
            {"type": "url_read", "status": "success", "url": ""},
            {"type": "url_read", "status": "success", "url": "https://example.com"},
            {"type": "knowledge_evidence", "status": "empty", "source_refs": []},
            {"type": "train_results", "status": "success", "trains": []},
            {"type": "route_results", "status": "degraded", "routes": [{"mode": "driving"}]},
            {"type": "file", "file_id": "仅上传引用"},
            {"type": "search"},
            {"type": "train_results"},
        ],
    )
    def test_失败或空壳块不算有效证据(self, block):
        assert has_tool_evidence([block]) is False

    @pytest.mark.parametrize(
        "block",
        [
            {"type": "knowledge_evidence", "status": "success", "source_refs": [{"evidence_id": "ev-1"}]},
            {"type": "train_results", "status": "success", "trains": [{"train_no": "G1234"}]},
            {"type": "route_results", "status": "degraded", "routes": [{"mode": "driving", "duration_s": 300}]},
            {"type": "weather_results", "status": "success", "forecast_days": [{"high_c": 0}]},
        ],
    )
    def test_有实际来源或结果的工具块被识别(self, block):
        assert has_tool_evidence([block]) is True


class TestResolveNoEvidenceAnswer:
    @pytest.mark.parametrize("candidate", [_FABRICATED_ANSWER, "明天香港 26 度，适合散步。", "可以坐 G1234 次列车。"])
    def test_没有能力快照时不解析回答措辞(self, candidate):
        """边界只看冻结能力与工具证据，不用正则从回答里找价格、车次、时长、气温。"""
        assert resolve_no_evidence_answer(candidate, content_blocks=[]) == (candidate, None)

    def test_收口文案不点名出行领域(self):
        """天气同属外部事实能力，不该收到班次/价格/时长的文案。

        真实验收 Run 8139d99f：天气请求收口时出现了出行文案。
        """
        answer, kind = resolve_no_evidence_answer(
            "明天香港 26 度，适合散步。", content_blocks=[], capability_resolution=_capability("weather")
        )
        assert kind == "required_external_evidence"
        assert answer == NO_EVIDENCE_ANSWER_TEXT
        for travel_word in ("班次", "价格", "时长", "票价", "车次"):
            assert travel_word not in NO_EVIDENCE_ANSWER_TEXT

    def test_有工具证据时一律放行(self):
        answer, kind = resolve_no_evidence_answer(
            _FABRICATED_ANSWER,
            content_blocks=[{"type": "train_results", "status": "success", "trains": [{"train_no": "G1234"}]}],
        )
        assert kind is None
        assert answer == _FABRICATED_ANSWER

    def test_零证据但回答诚实时放行(self):
        honest = "本次没能完成所需的查询，建议稍后重试。"
        answer, kind = resolve_no_evidence_answer(honest, content_blocks=[])
        assert kind is None
        assert answer == honest

    @pytest.mark.parametrize("package", ["fresh_web", "verified_web", "url_read", "weather", "mobility_intercity"])
    @pytest.mark.parametrize(
        "candidate", ["票价三百元，五小时到达。", "08:15 发车。", "该线路全天有票。", "查询没能完成。"]
    )
    def test_外部事实任务零证据按冻结能力收口而非猜测文本(self, package, candidate):
        answer, kind = resolve_no_evidence_answer(
            candidate,
            content_blocks=[],
            capability_resolution=_capability(package),
        )
        assert answer == NO_EVIDENCE_ANSWER_TEXT
        assert kind == "required_external_evidence"

    @pytest.mark.parametrize("package", ["direct", "transform", "date"])
    def test_不需要外部事实的能力不被动态数字误伤(self, package):
        candidate = "300 元加 300 元等于 600 元。"
        assert resolve_no_evidence_answer(
            candidate,
            content_blocks=[],
            capability_resolution=_capability(package),
        ) == (candidate, None)

    def test_知识证据不能为外部事实任务提供通行证(self):
        answer, kind = resolve_no_evidence_answer(
            "今天晴天。",
            capability_resolution=_capability("weather"),
            content_blocks=[
                {"type": "knowledge_evidence", "status": "success", "source_refs": [{"evidence_id": "ev-1"}]}
            ],
        )
        assert answer == NO_EVIDENCE_ANSWER_TEXT
        assert kind == "required_external_evidence"

    def test_成功网页恢复路径保留(self):
        candidate = "根据已读取资料，该线路全天有票。"
        result = ToolResult(status="success", data={"url": "https://example.com", "content": "今日班次全天有票。"})
        evidence = RecoveryEvidenceWorkset()
        evidence.record_result("url_read", result)
        block = UrlReadHandler().build_content_block(result, "url-block", "url-log")
        assert resolve_no_evidence_answer(
            candidate,
            capability_resolution=_capability("mobility_intercity"),
            content_blocks=[block],
            recovery_evidence=evidence,
        ) == (candidate, None)


class TestPrefetchedPageEvidence:
    """自动预读与续跑：预读块没有 source_refs，不能落进"零证据"（PR #72 复审）。"""

    def _prefetched_block(self, url: str = "https://example.com/a"):
        from app.schemas.chat import UrlBlock

        return UrlBlock(type="url_read", id="blk-pre", url=url, title="示例页面")

    def test_预读成功的正文算证据(self):
        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page("https://example.com/a")
        assert has_tool_evidence(
            [self._prefetched_block()],
            capability_resolution=_capability("url_read"),
            recovery_evidence=evidence,
        )

    def test_预读成功时正确答案不被换成兜底文案(self):
        candidate = "这篇文章说该型号续航 5 小时，售价 280 元。"
        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page("https://example.com/a")
        assert resolve_no_evidence_answer(
            candidate,
            capability_resolution=_capability("verified_web"),
            content_blocks=[self._prefetched_block()],
            recovery_evidence=evidence,
        ) == (candidate, None)

    def test_未登记的来源卡片仍不算证据(self):
        """续跑带回的历史块不登记，元数据不能重建证据。"""

        answer, kind = resolve_no_evidence_answer(
            _FABRICATED_ANSWER,
            capability_resolution=_capability("verified_web"),
            content_blocks=[self._prefetched_block()],
            recovery_evidence=RecoveryEvidenceWorkset(),
        )
        assert answer == NO_EVIDENCE_ANSWER_TEXT
        assert kind == "required_external_evidence"

    def test_只有链接的预读正文不算证据(self):
        """reader 的空正文判定放行"正文只有一个链接"，此处必须按工具口径拦下。"""

        from app.services.security.url_policy import UrlPolicyResult
        from app.services.stream.persistence import build_url_read_block

        policy = UrlPolicyResult(
            allowed=True,
            normalized_url="https://example.com/a",
            reason="ok",
            safe_log_url="https://example.com/a",
        )
        link_only = UrlReadResult(
            url="https://example.com/a",
            title="示例页面",
            content="https://example.com/a",
            favicon=None,
            content_length=21,
            fetch_ms=10,
        )
        block = build_url_read_block(
            read_result=link_only, policy=policy, detected_url="https://example.com/a", block_id="blk-pre"
        )
        assert block.status == "degraded"

        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page(block.url)
        answer, kind = resolve_no_evidence_answer(
            "这篇文章说该型号续航 5 小时，售价 280 元。",
            capability_resolution=_capability("url_read"),
            content_blocks=[block],
            recovery_evidence=evidence,
        )
        assert answer == NO_EVIDENCE_ANSWER_TEXT
        assert kind == "required_external_evidence"

    def test_真实正文的预读仍标成功并放行(self):
        from app.services.security.url_policy import UrlPolicyResult
        from app.services.stream.persistence import build_url_read_block

        policy = UrlPolicyResult(
            allowed=True,
            normalized_url="https://example.com/a",
            reason="ok",
            safe_log_url="https://example.com/a",
        )
        real = UrlReadResult(
            url="https://example.com/a",
            title="示例页面",
            content="该型号实测续航 5 小时，官方定价 280 元。",
            favicon=None,
            content_length=24,
            fetch_ms=10,
        )
        block = build_url_read_block(
            read_result=real, policy=policy, detected_url="https://example.com/a", block_id="blk-pre"
        )
        assert block.status == "success"

        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page(block.url)
        candidate = "这篇文章说该型号续航 5 小时，售价 280 元。"
        assert resolve_no_evidence_answer(
            candidate,
            capability_resolution=_capability("url_read"),
            content_blocks=[block],
            recovery_evidence=evidence,
        ) == (candidate, None)

    def test_预读失败的块不算证据(self):
        from app.schemas.chat import UrlBlock

        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page("https://example.com/a")
        failed = UrlBlock(type="url_read", id="blk-pre", url="https://example.com/a", status="failed")
        assert not has_tool_evidence(
            [failed], capability_resolution=_capability("url_read"), recovery_evidence=evidence
        )


@pytest.mark.parametrize("query", ["", "武汉到桂林最新车次"])
@pytest.mark.parametrize("description", ["", "https://example.com/timetable", "车次八点十五出发。"])
def test_真实搜索处理器只有实际正文可通过总结守卫(query, description):
    handler = WebSearchHandler()
    source = SearchSource(title="", url="https://example.com/timetable", description=description, content="")
    with patch("app.services.tool_handlers.web_search.search_web", new=AsyncMock(return_value=[source])):
        result = asyncio.run(handler.execute({"query": query}))
    block = handler.build_content_block(result, "search-block", "search-log")
    evidence = RecoveryEvidenceWorkset()
    evidence.record_result("web_search", result)
    usable = bool(query) and description == "车次八点十五出发。"
    assert has_recovery_evidence([block], evidence=evidence) is usable
    if not usable:
        assert has_tool_evidence([block]) is False
    assert has_tool_evidence([block], recovery_evidence=evidence) is usable
    candidate = "票价三百元，五小时，08:15 发车。"
    answer, kind = resolve_no_evidence_answer(
        candidate,
        content_blocks=[block],
        capability_resolution=_capability("fresh_web"),
        recovery_evidence=evidence,
    )
    assert (answer, kind) == ((candidate, None) if usable else (NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence"))
    assert has_tool_evidence([block]) is False


@pytest.mark.parametrize("content", [None, "https://example.com/timetable", "车次八点十五出发。"])
def test_真实网页处理器成功标记与URL不能代替正文(content):
    handler = UrlReadHandler()
    response = UrlReadResponse(
        result=UrlReadResult(
            url="https://example.com/timetable",
            title=None,
            content=content,
            favicon=None,
            content_length=len(content or ""),
            fetch_ms=1,
        )
    )
    with patch("app.services.tool_handlers.url_read.read_url_with_diagnostics", new=AsyncMock(return_value=response)):
        result = asyncio.run(handler.execute({"url": "https://example.com/timetable"}))
    block = handler.build_content_block(result, "url-block", "url-log")
    assert result.status == "success"
    evidence = RecoveryEvidenceWorkset()
    evidence.record_result("url_read", result)
    usable = content == "车次八点十五出发。"
    assert has_recovery_evidence([block], evidence=evidence) is usable
    if not usable:
        assert has_tool_evidence([block]) is False
    assert has_tool_evidence([block], recovery_evidence=evidence) is usable
    assert has_tool_evidence([block]) is False


class TestDiscoveryEvidenceRequirement:
    """动态发现没有能力快照，以本轮实际加载的外部工具作为事实需求。"""

    @staticmethod
    def _session(*names: str):
        from types import SimpleNamespace

        return SimpleNamespace(loaded_names={"tool_search", *names})

    def test_加载外部工具后零证据收口(self):
        answer, kind = resolve_no_evidence_answer(
            "杭州明天 28 度。", content_blocks=[], tool_discovery=self._session("weather_forecast")
        )
        assert (answer, kind) == (NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence")

    def test_只加载目录检索不构成事实需求(self):
        assert resolve_no_evidence_answer("你好。", content_blocks=[], tool_discovery=self._session()) == (
            "你好。",
            None,
        )


class TestMcpEvidence:
    """MCP 结果不落结果块：按工具身份登记成功取得的非空结果，失败与空结果不算证据。"""

    _ALIAS = "mcp_fare_lookup"

    @staticmethod
    def _mcp_capability() -> RunCapabilityResolution:
        return RunCapabilityResolution(
            schema_version=1,
            router_version="test",
            package_id="mcp_explicit",
            confidence="high",
            resolution_mode="routed",
            reason_codes=("explicit_authorized_tool_alias",),
            external_tool_names=("mcp_fare_lookup",),
            effective_plan_mode="off",
            include_current_date=False,
            network_boundary_required=False,
        )

    def _evidence(self, result: ToolResult, tool_name: str | None = None) -> RecoveryEvidenceWorkset:
        evidence = RecoveryEvidenceWorkset()
        evidence.record_result(tool_name or self._ALIAS, result)
        return evidence

    @pytest.mark.parametrize(
        "result",
        [
            ToolResult(status="failed", data={"error_code": "server_unavailable"}),
            ToolResult(status="success", data={"payload": {}}),
            ToolResult(status="success", data={"payload": None}),
        ],
    )
    def test_显式MCP失败或空结果后触顶收口(self, result):
        answer, kind = resolve_no_evidence_answer(
            "票价 300 元。",
            content_blocks=[],
            capability_resolution=self._mcp_capability(),
            recovery_evidence=self._evidence(result),
        )
        assert (answer, kind) == (NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence")

    def test_显式MCP成功时带数字的回答原样交付(self):
        result = ToolResult(status="success", data={"payload": {"fare_yuan": 300}})
        assert resolve_no_evidence_answer(
            "票价 300 元。",
            content_blocks=[],
            capability_resolution=self._mcp_capability(),
            recovery_evidence=self._evidence(result),
        ) == ("票价 300 元。", None)

    def test_非MCP工具名的payload不登记(self):
        evidence = self._evidence(
            ToolResult(status="success", data={"payload": {"x": 1}}), tool_name="weather_forecast"
        )
        assert evidence.mcp_tool_names == set()

    def test_动态发现加载MCP失败后收口(self):
        from types import SimpleNamespace

        session = SimpleNamespace(loaded_names={"tool_search", self._ALIAS})
        answer, kind = resolve_no_evidence_answer(
            "票价 300 元。",
            content_blocks=[],
            recovery_evidence=self._evidence(ToolResult(status="failed", data={})),
            tool_discovery=session,
        )
        assert (answer, kind) == (NO_EVIDENCE_ANSWER_TEXT, "required_external_evidence")

    def test_动态发现MCP成功时原样交付(self):
        from types import SimpleNamespace

        session = SimpleNamespace(loaded_names={"tool_search", self._ALIAS})
        result = ToolResult(status="success", data={"payload": {"fare_yuan": 300}})
        assert resolve_no_evidence_answer(
            "票价 300 元。",
            content_blocks=[],
            recovery_evidence=self._evidence(result),
            tool_discovery=session,
        ) == ("票价 300 元。", None)
