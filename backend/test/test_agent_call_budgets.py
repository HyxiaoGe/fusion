"""真实默认装配下，长任务不再被旧调用预算提前终止。"""

import asyncio
import unittest

from app.core import redis as redis_config
from app.core.config import Settings, settings
from app.services.mcp.agent_tools import McpAgentToolLimits, McpAgentToolRunBudget
from app.services.stream.agent_loop_policy import check_agent_loop_limit
from app.services.stream.runner import _agent_loop_limits


class AgentCallBudgetTests(unittest.TestCase):
    def test_default_run_continues_past_previous_limits(self):
        limits = _agent_loop_limits()
        for elapsed, rounds, calls in ((60, 8, 1), (60, 1, 20), (301, 1, 1), (900, 32, 100)):
            with self.subTest(elapsed=elapsed, rounds=rounds, calls=calls):
                self.assertIsNone(
                    check_agent_loop_limit(elapsed_seconds=elapsed, step=rounds, total_tool_calls=calls, limits=limits)
                )

    def test_extended_run_keeps_redis_ownership_until_its_deadline(self):
        limits = _agent_loop_limits()
        self.assertGreater(redis_config.LOCK_TTL, limits.total_timeout_s)
        self.assertGreater(redis_config.STREAM_CHUNK_TTL, limits.total_timeout_s)

    def test_mcp_defaults_allow_more_than_eight_subcalls(self):
        async def consume(max_calls):
            budget = McpAgentToolRunBudget(max_calls_per_server=max_calls)
            return [await budget.try_consume("amap-test") for _ in range(16)]

        for count in (
            McpAgentToolLimits().max_tool_calls_per_server_per_run,
            settings.MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN,
        ):
            with self.subTest(count=count):
                self.assertTrue(all(asyncio.run(consume(count))))

    def test_execution_limits_accept_explicit_configuration(self):
        configured = Settings(_env_file=None, AGENT_MAX_STEPS=96, AGENT_MAX_TOOL_CALLS=300, AGENT_TOTAL_TIMEOUT=2400)
        self.assertEqual(configured.AGENT_MAX_STEPS, 96)
        self.assertEqual(configured.AGENT_MAX_TOOL_CALLS, 300)
        self.assertEqual(configured.AGENT_TOTAL_TIMEOUT, 2400)
