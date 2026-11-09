"""装配工具前先刷新 agent-tools 网关的工具目录；游客也用网关的 web_search，同样刷新。"""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent import session_cache
from app.services.stream import runner
from test.services.stream.test_prompt_run_identity import run_kwargs


@pytest.mark.anyio
@pytest.mark.parametrize("guest", [False, True])
async def test_gateway_tool_catalog_is_refreshed_before_tools_are_assembled(guest):
    order = []

    async def refresh():
        order.append("refresh")

    def prepare(**_kwargs):
        order.append("prepare")
        raise RuntimeError("停在装配")

    dependencies = replace(
        runner._agent_loop_wiring_dependencies(),
        refresh_tool_catalog_fn=refresh,
        finalize_stream_fn=AsyncMock(),
    )
    with (
        patch.object(runner, "SessionLocal", return_value=MagicMock()),
        patch.object(runner, "_agent_loop_wiring_dependencies", return_value=dependencies),
        patch.object(session_cache, "write_session_started", AsyncMock()),
        patch.object(session_cache, "write_session_status", AsyncMock()),
        patch.object(runner, "prepare_agent_loop_call_config_inputs", side_effect=prepare),
        pytest.raises(RuntimeError, match="停在装配"),
    ):
        await runner.StreamHandler().generate_to_redis(**run_kwargs(), guest=guest)

    assert order == ["refresh", "prepare"]


def test_runner_wires_the_gateway_catalog_refresh():
    from app.services.external.agent_tools_mcp import refresh_gateway_tool_catalog

    assert runner._agent_loop_wiring_dependencies().refresh_tool_catalog_fn is refresh_gateway_tool_catalog
