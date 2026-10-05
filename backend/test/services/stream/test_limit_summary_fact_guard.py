"""触顶总结的工具证据判断（issue #30）：只决定是否补诚实下限提示，不替换回答。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.chat import SearchSource, TextBlock, ThinkingBlock
from app.services.external.reader_client import UrlReadResponse, UrlReadResult
from app.services.stream.limit_summary_fact_guard import has_tool_evidence
from app.services.stream.tool_recovery_evidence import RecoveryEvidenceWorkset, has_recovery_evidence
from app.services.tool_handlers.base import ToolResult
from app.services.tool_handlers.url_read import UrlReadHandler
from app.services.tool_handlers.web_search import WebSearchHandler


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


class TestPrefetchedPageEvidence:
    """自动预读与续跑：预读块没有 source_refs，不能落进"零证据"（PR #72 复审）。"""

    def _prefetched_block(self, url: str = "https://example.com/a"):
        from app.schemas.chat import UrlBlock

        return UrlBlock(type="url_read", id="blk-pre", url=url, title="示例页面")

    def test_预读成功的正文算证据(self):
        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page("https://example.com/a")
        assert has_tool_evidence([self._prefetched_block()], recovery_evidence=evidence)

    def test_未登记的来源卡片仍不算证据(self):
        """续跑带回的历史块不登记，元数据不能重建证据。"""

        assert not has_tool_evidence([self._prefetched_block()], recovery_evidence=RecoveryEvidenceWorkset())

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
        assert not has_tool_evidence([block], recovery_evidence=evidence)

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
        assert has_tool_evidence([block], recovery_evidence=evidence)

    def test_预读失败的块不算证据(self):
        from app.schemas.chat import UrlBlock

        evidence = RecoveryEvidenceWorkset()
        evidence.record_prefetched_page("https://example.com/a")
        failed = UrlBlock(type="url_read", id="blk-pre", url="https://example.com/a", status="failed")
        assert not has_tool_evidence([failed], recovery_evidence=evidence)


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


class TestMcpEvidence:
    """MCP 结果不落结果块：按工具身份登记成功取得的非空结果，失败与空结果不算证据。"""

    _ALIAS = "mcp_fare_lookup"

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
    def test_MCP失败或空结果不算证据(self, result):
        assert not has_tool_evidence([], recovery_evidence=self._evidence(result))

    def test_MCP成功结果算证据(self):
        result = ToolResult(status="success", data={"payload": {"fare_yuan": 300}})
        assert has_tool_evidence([], recovery_evidence=self._evidence(result))

    def test_非MCP工具名的payload不登记(self):
        evidence = self._evidence(
            ToolResult(status="success", data={"payload": {"x": 1}}), tool_name="weather_forecast"
        )
        assert evidence.mcp_tool_names == set()
