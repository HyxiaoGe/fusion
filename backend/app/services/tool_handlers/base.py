"""
Tool Handler 抽象基类 — 所有工具处理器的共性接口和共享能力
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from app.core.logger import app_logger as logger
from app.schemas.chat import ContentBlock
from app.services.agent_logger import log_tool_call
from app.services.tool_detail_snapshot import build_tool_detail_snapshot

if TYPE_CHECKING:
    from app.services.agent.context_broker import Geolocation


@dataclass
class ToolRuntimeContext:
    """执行工具时由 agent loop 提供的运行内信息；工具按需读取，不需要的忽略。"""

    geolocation: Geolocation | None = None
    step_number: int = 0
    route_city_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    remaining_agent_tool_calls: int | None = None
    # 本 Run 已投影的结果块快照，供需要引用已完成查询的工具（如文档数据来源）读取。
    content_blocks: tuple[Any, ...] = ()


@dataclass
class ToolResult:
    """工具执行结果"""

    status: str  # "success" | "failed" | "degraded"
    data: dict = field(default_factory=dict)
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    trajectory_log_task: asyncio.Task | None = field(default=None, repr=False, compare=False)


def _serialize_for_json(obj):
    """递归序列化，确保所有 Pydantic 对象转为 dict"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    return obj


def _task_done_callback(task: asyncio.Task):
    """asyncio.create_task 异常回调，确保日志写入失败可见"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("日志写入异步任务异常: error_type=%s", type(exc).__name__)


def _capture_trajectory_detail(input_params: dict, result: ToolResult, output_data: dict) -> dict:
    """从执行侧对象复制业务详情；辅助记录失败不改变工具执行结果。"""
    try:
        return build_tool_detail_snapshot(
            _serialize_for_json(input_params),
            _serialize_for_json(output_data),
            result.error_message,
        )
    except Exception as exc:
        logger.warning("工具详情采集失败: error_type=%s", type(exc).__name__)
        return {"schema_version": 1, "capture_failed": True}


class BaseToolHandler(ABC):
    """所有 tool handler 的抽象基类"""

    supports_run_level_citations = False
    supports_automatic_retry = True
    # 失败结果是否附带「换用其他工具继续」的恢复指引；只校验模型引用的输出工具关掉。
    suggests_alternatives_on_failure = True
    # 回填给模型的工具消息是否原文存进工具日志供轨迹查看；含用户文档正文的工具关掉。
    persists_model_observation = True

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """工具名称，用于日志和注册"""

    @property
    def display_label(self) -> str | None:
        """工具名本身不可读时（如 MCP 哈希别名）给轨迹显示的名称；内置工具由前端登记中文名。"""
        return None

    @property
    @abstractmethod
    def sse_event_prefix(self) -> str:
        """SSE 事件前缀，如 'search' → search_start/search_complete"""

    @abstractmethod
    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        """执行工具核心逻辑；context 由 agent loop 传入，直接调用（测试、组合工具）时可省略。"""

    @abstractmethod
    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> ContentBlock | None:
        """构造落库用的 content block"""

    @abstractmethod
    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        """格式化注入第二轮 LLM 的上下文文本"""

    # ---- 共享能力 ----

    async def log(
        self,
        log_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        provider: str,
        result: ToolResult,
        input_params: dict,
        trace_id: str = None,
        tool_call_id: str | None = None,
        step_number: int = None,
        message_id: str | None = None,
    ) -> None:
        """异步记录 ToolCallLog"""
        safe_input_params = self.sanitize_input_params_for_log(input_params)
        safe_output_data = self.sanitize_output_data_for_log(result)
        task = asyncio.create_task(
            log_tool_call(
                log_id=log_id,
                conversation_id=conversation_id,
                message_id=message_id,
                user_id=user_id,
                tool_name=self.tool_name,
                status=result.status,
                duration_ms=result.duration_ms,
                model_id=model_id,
                provider=provider,
                input_params=safe_input_params,
                output_data=_serialize_for_json(safe_output_data),
                metadata={
                    "trajectory_detail": _capture_trajectory_detail(
                        input_params, result, self.trajectory_output_data(result)
                    )
                },
                error_message=result.error_message,
                trace_id=trace_id,
                tool_call_id=tool_call_id,
                step_number=step_number,
            )
        )
        result.trajectory_log_task = task
        task.add_done_callback(_task_done_callback)

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        """子类可覆盖以清理即将持久化的工具入参。"""
        return input_params

    def sanitize_input_params_for_event(self, input_params: dict) -> dict:
        """子类可覆盖以清理进度事件里的工具入参。"""
        return input_params

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        """子类可覆盖以限制即将持久化的工具输出。"""
        return result.data

    def trajectory_output_data(self, result: ToolResult) -> dict:
        """子类可覆盖以排除不应随轨迹永久保存的输出（如用户文档正文）。"""
        return result.data

    def build_successful_call_signature(self, input_params: dict) -> str | None:
        """为可安全复用的只读调用生成运行内签名。

        默认禁用，避免未来有副作用的工具被错误去重。只有能保证“同名 + 归一化参数”
        成功结果可在同一 Agent run 内复用的 handler 才应覆盖。
        """

        return None

    # ---- 可选能力：子类按需覆盖，默认不启用 ----

    # 已知慢工具（如生图）可声明更长的单次超时；None 沿用统一上限。
    execution_timeout_seconds: float | None = None

    def check_runtime_arguments(self, args: dict, context: ToolRuntimeContext | None) -> dict | None:
        """真实执行前按运行内信息检查参数；返回 {"error_code", "validation_errors", "hints"?} 则本次不触网。"""

        return None

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """真实执行前的本地参数校验；返回 [{"field", "code"}] 则不触网，错误交还模型。"""

        return []

    def build_local_preflight_result(self, validation_errors: list[str]) -> ToolResult | None:
        """参数校验失败时投影工具自己的状态型结果；None 走通用参数错误结果。"""

        return None

    async def is_run_budget_exhausted(self) -> bool:
        """本 Run 的服务额度是否已用完；用完后下一轮不再公告该工具。"""

        return False

    def _build_result_summary(self, result: "ToolResult") -> dict:
        """子类可覆盖返回轻量摘要（如搜索命中数 / favicon）。

        默认返回最小 {kind, truncated}。

        注意：返回值会被 emitter.tool_call_completed 内部的
        cap_and_truncate(max_bytes=1024) 兜底截断（含递归嵌套），
        子类无需自己截断字符串字段。
        """
        return {"kind": self.tool_name, "truncated": False}
