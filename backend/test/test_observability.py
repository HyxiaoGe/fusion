"""日志关联字段、JSON 日志格式与 fusion-api 指标。"""

import asyncio
import io
import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.core.logger import _configure_handler, log_context
from app.services.stream.sse_encoder import stream_redis_as_sse
from app.services.stream.tool_executor import execute_tools_parallel
from app.services.tool_handlers.base import ToolResult
from test.fake_tool_handler import fake_tool_handler


def _sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0


class _CapturedLogs:
    """挂一个与线上同样配置的 handler，读回格式化后的 JSON 行。"""

    def __init__(self, logger_name: str):
        self.stream = io.StringIO()
        self.logger = logging.getLogger(logger_name)
        self.handler = _configure_handler(logging.StreamHandler(self.stream))

    def __enter__(self):
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *_exc):
        self.logger.removeHandler(self.handler)

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]


class JsonLogContextTests(unittest.IsolatedAsyncioTestCase):
    def test_lines_carry_bound_ids_and_extra_fields_only_inside_context(self):
        logger = logging.getLogger("test.observability.bound")
        with _CapturedLogs(logger.name) as captured:
            with log_context(request_id="req-1"):
                with log_context(run_id="run-1", conversation_id="conv-1"):
                    logger.info("inner %s", "msg", extra={"tool": "web_search"})
                logger.warning("outer")
            logger.info("unbound")

        inner, outer, unbound = captured.lines()
        self.assertEqual(inner["msg"], "inner msg")
        self.assertEqual(inner["level"], "INFO")
        self.assertEqual(inner["logger"], logger.name)
        self.assertEqual(
            (inner["request_id"], inner["run_id"], inner["conversation_id"], inner["tool"]),
            ("req-1", "run-1", "conv-1", "web_search"),
        )
        self.assertEqual(outer["request_id"], "req-1")
        self.assertNotIn("run_id", outer)
        self.assertNotIn("request_id", unbound)

    def test_exception_traceback_stays_in_one_json_line(self):
        logger = logging.getLogger("test.observability.exc")
        with _CapturedLogs(logger.name) as captured:
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                logger.exception("failed")

        (line,) = captured.lines()
        self.assertEqual(line["level"], "ERROR")
        self.assertIn("RuntimeError: boom", line["exc"])

    async def test_context_follows_background_tasks_and_threads(self):
        logger = logging.getLogger("test.observability.async")
        with _CapturedLogs(logger.name) as captured:
            with log_context(run_id="run-bg"):
                await asyncio.create_task(_log_async(logger, "task"))
                await asyncio.to_thread(logger.info, "thread")

        self.assertEqual([line["run_id"] for line in captured.lines()], ["run-bg", "run-bg"])

    def test_request_middleware_binds_response_request_id(self):
        from main import add_request_id

        logger = logging.getLogger("test.observability.request")
        app = FastAPI()
        app.middleware("http")(add_request_id)

        @app.get("/probe")
        async def probe():
            logger.info("handled")
            return {}

        with _CapturedLogs(logger.name) as captured:
            response = TestClient(app).get("/probe")

        (line,) = captured.lines()
        self.assertEqual(line["request_id"], response.headers["X-Request-ID"])


async def _log_async(logger: logging.Logger, message: str) -> None:
    logger.info(message)


class SseStreamGaugeTests(unittest.IsolatedAsyncioTestCase):
    async def test_gauge_counts_open_stream_until_client_disconnects(self):
        async def read_stream_chunks(*_args, **_kwargs):
            yield {"entry_id": "1-0", "type": "answering", "content": "答", "block_id": "blk-1"}
            await asyncio.Event().wait()

        before = _sample("fusion_sse_streams_active")
        with (
            patch("app.core.redis.get_redis_pool", return_value=object()),
            patch("app.services.stream.sse_encoder.read_stream_chunks", new=read_stream_chunks),
        ):
            stream = stream_redis_as_sse("conv-1", "msg-1", "task-1")
            await asyncio.wait_for(anext(stream), timeout=1)
            self.assertEqual(_sample("fusion_sse_streams_active"), before + 1)
            await stream.aclose()

        self.assertEqual(_sample("fusion_sse_streams_active"), before)


class ToolCallMetricTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_status_and_duration_and_folds_unknown_tool_names(self):
        handler = fake_tool_handler(
            tool_name="metric_probe",
            execute=AsyncMock(return_value=ToolResult(status="failed", error_message="x", duration_ms=1500)),
            log=AsyncMock(),
        )
        failed_before = _sample("fusion_tool_calls_total", tool="metric_probe", status="failed")
        unknown_before = _sample("fusion_tool_calls_total", tool="unknown", status="failed")
        duration_before = _sample("fusion_tool_call_duration_seconds_count", tool="metric_probe")

        await execute_tools_parallel(
            [
                {"id": "call-1", "name": "metric_probe", "arguments": "{}"},
                {"id": "call-2", "name": "made_up_tool_name", "arguments": "{}"},
            ],
            "conv-1",
            "user-1",
            "model-1",
            "openai",
            tool_handlers={"metric_probe": handler},
        )

        self.assertEqual(_sample("fusion_tool_calls_total", tool="metric_probe", status="failed"), failed_before + 1)
        self.assertEqual(_sample("fusion_tool_calls_total", tool="unknown", status="failed"), unknown_before + 1)
        self.assertEqual(_sample("fusion_tool_calls_total", tool="made_up_tool_name", status="failed"), 0)
        self.assertEqual(_sample("fusion_tool_call_duration_seconds_count", tool="metric_probe"), duration_before + 1)
