"""fusion-api 的 Prometheus 指标。

只统计会静默发生、事后难从日志拼出趋势的事件：备用模型切换、熔断、模型不可用、工具结果与
run 终态，以及进行中的 run 与 SSE 连接数。单次 run 的细节仍以轨迹为准，这里不记 run/会话 ID。

注册表在进程内：dev 的 fusion-api 以单 worker 运行；多 worker 时只有先绑定端口的进程暴露指标。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from app.core.logger import app_logger as logger

AGENT_RUNS_ACTIVE = Gauge("fusion_agent_runs_active", "进行中的 agent run 数")
AGENT_RUNS_TOTAL = Counter("fusion_agent_runs_total", "已结束的 agent run 数，按终态", ["status"])
SSE_STREAMS_ACTIVE = Gauge("fusion_sse_streams_active", "正在向客户端推送的 SSE 连接数")
TOOL_CALLS_TOTAL = Counter("fusion_tool_calls_total", "工具调用结果数", ["tool", "status"])
TOOL_CALL_DURATION_SECONDS = Histogram(
    "fusion_tool_call_duration_seconds",
    "工具调用耗时（不含复用的结果）",
    ["tool"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60),
)
MODEL_ROUND_UNAVAILABLE_TOTAL = Counter(
    "fusion_model_round_unavailable_total",
    "模型本轮服务端不可用（首字超时、5xx、429、连接错误）次数",
    ["model", "reason"],
)
MODEL_FALLBACK_TOTAL = Counter(
    "fusion_model_fallback_total",
    "本次回答改用备用模型的次数；fallback=none 表示没有可换的备用模型",
    ["model", "fallback", "reason"],
)
MODEL_CIRCUIT_OPENED_TOTAL = Counter("fusion_model_circuit_opened_total", "模型熔断打开次数", ["model"])

_UNKNOWN_TOOL = "unknown"


def observe_tool_call(*, tool_name: str, known: bool, status: str, reused: bool, duration_ms: int | None) -> None:
    # 模型臆造的工具名不受控，不能直接作为标签值。
    tool = tool_name if known else _UNKNOWN_TOOL
    TOOL_CALLS_TOTAL.labels(tool=tool, status="reused" if reused else status).inc()
    if not reused and duration_ms is not None:
        TOOL_CALL_DURATION_SECONDS.labels(tool=tool).observe(duration_ms / 1000)


def start_metrics_server(port: int) -> None:
    if port <= 0:
        return
    try:
        start_http_server(port)
    except OSError as error:
        logger.warning("指标端口启动失败，本进程不暴露指标: port=%s error_type=%s", port, type(error).__name__)
        return
    logger.info("指标端口已启动: port=%s", port)
