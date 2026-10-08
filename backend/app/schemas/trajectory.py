"""轨迹历史读侧的稳定 DTO。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.run_capability_contract import CAPABILITY_CONTROL_TOOL_NAMES, DEEP_RESEARCH_TOOL_NAMES

CapabilityModeId = Literal["agent", "deep_research", "knowledge_grounded", "tools_unavailable"]
CapabilityReasonCode = Literal[
    "all_available_tools",
    "deep_research_mode",
    "knowledge_grounded_mode",
    "tools_disabled",
    "function_calling_unavailable",
    "search_capability_unavailable",
    "required_tools_unavailable",
]
_TOOL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")


class TrajectoryCapabilityResolution(BaseModel):
    """Run 级工具边界在实时与历史协议中的显式安全 DTO。

    v3 起不再有能力包分类，package_id 只表示模式。旧版本记录读侧校验失败即视为不可读，
    不回溯兼容。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[3]
    router_version: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}\.\d+$", max_length=32)
    package_id: CapabilityModeId
    reason_codes: list[CapabilityReasonCode] = Field(min_length=1, max_length=2)
    external_tool_names: list[str] = Field(max_length=128)
    deferred_tool_names: list[str] = Field(default_factory=list, max_length=512)
    effective_plan_mode: Literal["on", "off"]
    network_boundary_required: bool
    bundle_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("reason_codes", "external_tool_names", "deferred_tool_names")
    @classmethod
    def _require_unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("能力路由列表字段不得重复")
        return value

    @field_validator("external_tool_names", "deferred_tool_names")
    @classmethod
    def _validate_tool_names(cls, value: list[str]) -> list[str]:
        if any(_TOOL_NAME_RE.fullmatch(name) is None for name in value):
            raise ValueError("能力路由工具名格式非法")
        return value

    @model_validator(mode="after")
    def _validate_mode_semantics(self) -> TrajectoryCapabilityResolution:
        if set(self.external_tool_names).intersection(self.deferred_tool_names):
            raise ValueError("按需加载工具不得同时直接公告")
        if CAPABILITY_CONTROL_TOOL_NAMES.intersection([*self.external_tool_names, *self.deferred_tool_names]):
            raise ValueError("控制工具不属于外部工具")
        if self.package_id == "deep_research" and (
            self.deferred_tool_names or not set(self.external_tool_names) <= set(DEEP_RESEARCH_TOOL_NAMES)
        ):
            raise ValueError("深度研究只公告联网搜索与网页读取")
        has_tools = bool(self.external_tool_names or self.deferred_tool_names)
        if self.package_id in {"knowledge_grounded", "tools_unavailable"} and has_tools:
            raise ValueError("无工具模式不得公告工具")
        if self.network_boundary_required == has_tools:
            raise ValueError("联网边界必须与是否有工具一致")
        return self


@dataclass(frozen=True)
class UserTrajectoryMetaRow:
    """普通读取所需的窄 meta 数据，不携带 terminal intent 详情。"""

    trajectory_status: str
    event_count: int
    expected_last_sequence: int | None
    degraded_reason: str | None
    has_pending_terminal_intent: bool
    llm_detail_schema_version: int | None
    llm_round_count: int


class TrajectoryEventRecord(BaseModel):
    """从账本读取后交给纯投影器的事件记录。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    sequence: int
    event_type: str
    schema_version: int | None = None
    timestamp: datetime
    step_id: str | None = None
    tool_call_id: str | None = None
    parent_step_id: str | None = None
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TrajectoryRecord(BaseModel):
    """账本事件在快照中的只读投影。"""

    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_type: str
    schema_version: int
    timestamp: datetime
    step_id: str | None = None
    tool_call_id: str | None = None
    parent_step_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    payload: dict[str, Any]


class TrajectorySpan(BaseModel):
    """由账本生命周期事件重建出的执行区间。"""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    kind: str
    name: str
    parent_span_id: str | None = None
    start_sequence: int
    end_sequence: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    status: str
    terminal_source: str | None = None
    inferred_reason: str | None = None
    ttft_ms: int | None = None
    record_sequences: list[int] = Field(default_factory=list)


class TrajectoryProjection(BaseModel):
    """单个 run 的事件与 span 投影结果。"""

    model_config = ConfigDict(extra="forbid")

    records: list[TrajectoryRecord] = Field(default_factory=list)
    spans: list[TrajectorySpan] = Field(default_factory=list)


class TrajectoryPromptBundleIdentity(BaseModel):
    """正文缺失或降级时仍可读取的最小 Prompt 版本身份。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    source_kind: Literal["code_default"]
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_source_identity(self):
        if self.source_revision is not None:
            raise ValueError("本地 Prompt 不能携带远端发布身份")
        return self


class TrajectoryRunSummary(BaseModel):
    """AgentSession 权威摘要在普通读取端点中的稳定形状。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    message_id: str | None = None
    turn_message_id: str | None = None
    attempt_index: int | None = None
    status: str
    trajectory_status: str
    total_steps: int
    total_tool_calls: int
    duration_ms: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    llm_detail_schema_version: int | None = None
    llm_round_count: int = 0
    capability_resolution: TrajectoryCapabilityResolution | None = None
    prompt_bundle: TrajectoryPromptBundleIdentity | None = None


class TrajectoryRunListResponse(BaseModel):
    """会话内有界的 run 尝试列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[TrajectoryRunSummary] = Field(default_factory=list)
    truncated: bool = False


class TrajectoryCompleteness(BaseModel):
    """账本读取前缀与持久化完整性状态。"""

    model_config = ConfigDict(extra="forbid")

    status: str
    degraded_reason: str | None = None
    event_count: int | None = None
    expected_last_sequence: int | None = None
    loaded_event_count: int
    first_sequence: int | None = None
    last_sequence: int | None = None


class TrajectoryLlmRoundSummary(BaseModel):
    """快照中用于高密度账本展示的有界 LLM 正文预览。"""

    model_config = ConfigDict(extra="forbid")

    llm_round_id: str
    reasoning_preview: str | None = None
    output_preview: str | None = None


class TrajectorySnapshot(BaseModel):
    """普通用户可读取的脱敏账本快照。"""

    model_config = ConfigDict(extra="forbid")

    run: TrajectoryRunSummary
    records: list[TrajectoryRecord] = Field(default_factory=list)
    spans: list[TrajectorySpan] = Field(default_factory=list)
    completeness: TrajectoryCompleteness
    truncated: bool = False
    llm_round_summaries: list[TrajectoryLlmRoundSummary] = Field(default_factory=list)


class ToolDetailSnapshot(BaseModel):
    """工具调用时保存的业务详情；与审计摘要分开，不接受损坏格式。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=1)
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str | None
    redacted_fields: list[str] = Field(max_length=64)
    truncated_fields: list[str] = Field(max_length=64)


class ToolObservation(BaseModel):
    """应用实际回填的工具反馈；旧记录禁止根据原始结果重建。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    status: Literal["available", "not_recorded", "capture_failed"] = "not_recorded"
    text: str | None = Field(default=None, max_length=32768)
    original_chars: int | None = Field(default=None, ge=0)
    llm_round_id: str | None = Field(default=None, max_length=128)
    generated_round_index: int | None = Field(default=None, ge=1)
    redacted_fields: list[str] = Field(default_factory=list, max_length=64)
    truncated_fields: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _require_captured_text(self) -> ToolObservation:
        if self.status == "available" and (not self.text or self.original_chars is None):
            raise ValueError("可用反馈必须包含实际采集正文及原始字符数")
        return self


class ContextToolVisibility(BaseModel):
    """本应用裁剪前后的工具消息 ID，非供应商 HTTP 请求全文。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    scope: Literal["application_messages_after_context_management"]
    context_status: str = Field(pattern=r"^[a-z_]{1,64}$")
    before_tool_call_ids: list[str] = Field(max_length=200)
    visible_tool_call_ids: list[str] = Field(max_length=200)
    removed_tool_call_ids: list[str] = Field(max_length=200)
    before_count: int = Field(ge=0)
    visible_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    truncated: bool


class ToolNodeDetail(BaseModel):
    """普通用户可读取的 Tool 节点安全详情。"""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    tool_name: str
    status: str
    duration_ms: int | None = None
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    observation: ToolObservation = Field(default_factory=ToolObservation)


class LlmOutputProvenance(BaseModel):
    """单轮正文的实际处置，不携带候选或答案全文。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    disposition: Literal["emitted", "suppressed", "replaced"]
    source: Literal["model", "server", "none"]
    reason: Literal[
        "streamed",
        "deferred",
        "server_rewrite",
        "product_guard",
        "knowledge_guard",
        "plan_continues",
        "tool_round",
        "tool_retracted",
        "content_filtered",
        "research_guard",
        "summary_guard",
        "no_content",
        "not_committed",
        "round_failed",
        "round_cancelled",
    ]
    block_id: str | None = Field(default=None, min_length=1, max_length=128)


class LlmNodeDetail(BaseModel):
    """普通用户可读取的单个 LLM Round 正文详情。"""

    model_config = ConfigDict(extra="forbid")

    llm_round_id: str
    reasoning_text: str | None = None
    output_text: str | None = None
    output_provenance: LlmOutputProvenance | None = None
    context_visibility: ContextToolVisibility | None = None


class SystemPromptSection(BaseModel):
    """运行时实际组装并持久化的有序系统提示词段落。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    section_id: str = Field(min_length=1)
    content: str


class SystemPromptNodeDetail(BaseModel):
    """仅通过独立详情端点返回的历史系统提示词正文。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    template_version: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    char_count: int = Field(ge=0)
    sections: list[SystemPromptSection] = Field(min_length=1)


class SystemPromptSnapshot(SystemPromptNodeDetail):
    """持久化格式校验；版本号不接受布尔值或字符串隐式转换。"""

    schema_version: int = Field(ge=1, le=1)


class TrajectoryNodeDetailResponse(BaseModel):
    """轨迹节点详情的统一稳定响应信封。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "pending", "not_recorded", "degraded"]
    node_type: Literal["tool", "llm", "system_prompt"] = "tool"
    available_sections: list[
        Literal["summary", "payload", "result", "timing", "schema", "thinking", "output", "prompt"]
    ] = Field(default_factory=list)
    detail: ToolNodeDetail | LlmNodeDetail | SystemPromptNodeDetail | None = None
    redacted_fields: list[str] = Field(default_factory=list)
    truncated_fields: list[str] = Field(default_factory=list)
    reason: str | None = None
