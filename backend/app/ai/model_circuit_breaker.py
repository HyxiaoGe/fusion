"""模型可用性熔断：真实请求连续出现服务端不可用时，短时间内不再先试这个模型。

只统计服务商侧的不可用（首字超时、5xx、429、连接与超时），请求本身的错误（400、
审核拦截）不算。熔断打开期间，Agent loop 直接用备用模型、自动选择把它排到最后；
冷却结束后放行下一次请求试探，成功即复位，失败立即再次打开。

状态只在本进程内存里：dev 的 fusion-api 单 worker 运行，与 MCP 服务熔断同一取舍。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.logger import app_logger as logger
from app.core.metrics import MODEL_CIRCUIT_OPENED_TOTAL

# 近 7 天首字超时成簇出现（同一模型几分钟内 2~3 次），连续 2 次即判定为服务商侧故障。
MODEL_CIRCUIT_FAILURE_THRESHOLD = 2
MODEL_CIRCUIT_COOLDOWN_S = 180.0


@dataclass
class _ModelCircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None


class ModelCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = MODEL_CIRCUIT_FAILURE_THRESHOLD,
        cooldown_seconds: float = MODEL_CIRCUIT_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1 or cooldown_seconds <= 0:
            raise ValueError("模型熔断配置无效")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._states: dict[str, _ModelCircuitState] = {}
        self._lock = threading.Lock()

    def is_open(self, model_id: str) -> bool:
        """冷却期内返回 True；冷却结束即半开，放行请求试探。"""

        with self._lock:
            state = self._states.get(model_id)
            return (
                state is not None
                and state.opened_at is not None
                and self.clock() - state.opened_at < self.cooldown_seconds
            )

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def record_success(self, model_id: str) -> None:
        with self._lock:
            previous = self._states.pop(model_id, None)
        if previous is not None and previous.opened_at is not None:
            logger.info("模型熔断复位: model_id=%s", model_id)

    def record_failure(self, model_id: str, reason: str) -> None:
        with self._lock:
            state = self._states.setdefault(model_id, _ModelCircuitState())
            state.consecutive_failures += 1
            if state.consecutive_failures < self.failure_threshold:
                return
            # 半开试探失败也走这里：连续失败数未清零，直接重新计时。
            state.opened_at = self.clock()
            failures = state.consecutive_failures
        MODEL_CIRCUIT_OPENED_TOTAL.labels(model=model_id).inc()
        logger.warning(
            "模型熔断打开: model_id=%s consecutive_failures=%s reason=%s cooldown_s=%g",
            model_id,
            failures,
            reason,
            self.cooldown_seconds,
        )


model_circuit_breaker = ModelCircuitBreaker()
