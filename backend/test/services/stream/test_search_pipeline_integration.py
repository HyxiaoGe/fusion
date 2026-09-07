"""多角度并行检索及后续检索、阅读的真实流水线隔离回归。"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.chat import SearchSource
from app.services.tool_handlers.url_read import UrlReadHandler
from app.services.tool_handlers.web_search import WebSearchHandler


def _call(identifier, name, **arguments):
    return {"id": identifier, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


def _round(*calls):
    return ("", "", list(calls), "tool_calls", None)


async def _run_parallel_searches(width):
    from test.services.stream.test_agent_loop_contract import AgentLoopContractTests

    harness = AgentLoopContractTests()
    harness.setUp()
    angles = ["芯片出口管制政策原文", "算力租赁成本财报", "开源模型推理基准", "数据中心能源需求统计"][:width]
    queries = [*angles, "投资回报率敏感性分析", "关键结论反例交叉核实"]
    source_urls = {query: f"https://source-{index}.example.org/report" for index, query in enumerate(queries, 1)}
    entered = set()
    completed = set()
    all_entered = asyncio.Event()
    concurrency_observations = []
    provider_calls = []

    async def search_provider(query, **kwargs):
        provider_calls.append((query, kwargs))
        if query in angles:
            entered.add(query)
            if len(entered) == width:
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), timeout=2)
            concurrency_observations.append((len(entered), len(completed)))
        completed.add(query)
        return [
            SearchSource(title=query, url=source_urls[query], description=f"独立证据：{query}；含原始数据与分析结论。")
        ]

    calls = [_call(f"angle-{index}", "web_search", query=query, count=10 + index) for index, query in enumerate(angles)]
    rounds = [
        _round(*calls),
        _round(_call("followup-2", "web_search", query=queries[-2], count=16)),
        _round(_call("followup-3", "web_search", query=queries[-1], count=18)),
        _round(_call("read-4", "url_read", url=source_urls[queries[-1]], reason="核实交叉分析中的原始数据")),
        ("", "综合多角度来源完成分析。[1]", [], "stop", None),
    ]
    reader_result = SimpleNamespace(
        result=SimpleNamespace(
            url=source_urls[queries[-1]],
            title="原始分析报告",
            content="报告正文和原始统计数据。",
            favicon=None,
            content_length=14,
            fetch_ms=1,
            attempts=1,
        ),
        failure=None,
    )
    tools = SimpleNamespace(
        definitions=[], handlers={"web_search": WebSearchHandler(), "url_read": UrlReadHandler()}, audit_bindings=[]
    )
    with (
        patch("app.services.tool_handlers.base.BaseToolHandler.log", AsyncMock(return_value=None)),
        patch("app.services.tool_handlers.web_search.search_web", side_effect=search_provider),
        patch(
            "app.services.tool_handlers.url_read.read_url_with_diagnostics", AsyncMock(return_value=reader_result)
        ) as reader,
    ):
        result = await harness._run_agent_contract(
            rounds=rounds,
            use_real_tool_executor=True,
            dynamic_tool_set=tools,
            capabilities={"functionCalling": True, "agentTools": True, "searchCapable": True},
            user_message="请联网搜索最新AI算力投资趋势，从政策、经济、技术和能源多个角度分析，并阅读原文交叉核实来源。",
        )
    return result, provider_calls, concurrency_observations, source_urls, reader.await_count


class SearchPipelineIntegrationTests(unittest.TestCase):
    def test_multiple_search_calls_run_in_parallel_and_continue_across_rounds(self):
        for width in (2, 3, 4):
            with self.subTest(width=width):
                result, provider_calls, observations, source_urls, reader_calls = asyncio.run(
                    _run_parallel_searches(width)
                )
                self.assertEqual(len(provider_calls), width + 2)
                self.assertEqual(len(observations), width)
                self.assertTrue(all(entered == width for entered, _ in observations))
                self.assertEqual(observations[0][1], 0)
                self.assertEqual([args["count"] for _, args in provider_calls], [*range(10, 10 + width), 16, 18])
                self.assertEqual(reader_calls, 1)
                expected_ids = [*[f"angle-{index}" for index in range(width)], "followup-2", "followup-3", "read-4"]
                completed = [event for event in result.events if event["type"] == "tool_call_completed"]
                self.assertEqual({event["tool_call_id"] for event in completed}, set(expected_ids))
                self.assertEqual(len(completed), len(expected_ids))
                messages = result.llm_calls[-1]["messages"]
                tool_messages = [message for message in messages if message["role"] == "tool"]
                self.assertEqual([message["tool_call_id"] for message in tool_messages], expected_ids)
                for index, (query, url) in enumerate(source_urls.items(), 1):
                    observation = tool_messages[index - 1]["content"]
                    self.assertIn(query, observation)
                    self.assertIn(url, observation)
                    self.assertIn(f"[{index}]", observation)
                self.assertIn("报告正文和原始统计数据", tool_messages[-1]["content"])
                self.assertIn(f"[{width + 2}]", tool_messages[-1]["content"])
                self.assertNotIn("U1", tool_messages[-1]["content"])
                self.assertEqual(result.session_status_calls[-1]["status"], "completed")
