"""Run 内能力升级：首判能力不足时，作答模型可在同一 Run 内申请一次升级。

是否申请完全由模型判断，服务端只做硬校验：每个 Run 最多升级一次、目标包在可升级
集合内、首判时用户的禁用与禁网约束原样继承、目标包工具对当前模型与账号可用。
校验复用分类器的 build_candidate_route，派生复用首判的 build_agent_loop_call_config，
所以升级后的工具、计划、证据守卫与 Skill 目录与「首判就是目标包」完全一致。

handler 只登记待升级的配置；driver 在下一轮开始前原子切换执行面与系统提示词，
保证同一轮内模型看到的工具目录与服务端执行权限始终一致。
设计见 docs/specs/backend/2026-10-03-run-capability-escalation.md。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.ai.prompts.prompt_message import to_provider_messages
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.chat.model_call_language_policy import finalize_model_call_language_policy
from app.services.stream.run_capability_model_classifier import build_candidate_route
from app.services.tool_handlers.base import BaseToolHandler, ToolResult
from app.utils.prompt_fingerprint import fingerprint_system_messages
from app.utils.run_capability_contract import CAPABILITY_PACKAGES

if TYPE_CHECKING:
    from app.ai.prompts.prompt_message import PromptMessage
    from app.services.stream.agent_loop_runtime import AgentLoopRuntime
    from app.services.stream.agent_loop_state import AgentLoopState
    from app.services.stream.run_capability_router import RunCapabilityResolution, _CandidateRoute

logger = logging.getLogger(__name__)

REQUEST_CAPABILITY_TOOL_NAME = "request_capability"
# 只从零外部工具的首判升级：已有外部工具的包之间不平移，也不降级。
ESCALATION_SOURCE_PACKAGES = frozenset({"direct", "clarification_only"})
# 被拒后允许模型修正一次参数；成功升级始终只有一次。
MAX_ESCALATION_REQUESTS_PER_RUN = 2
# 参数层面的拒绝：模型改参数后可以再申请一次；其余拒绝原因改参数也无济于事。
_RETRYABLE_REASON_CODES = frozenset({"unknown_package", "invalid_tool_selection", "primary_tool_name_required"})
_MAX_REASON_CHARS = 200


def escalation_target_package_ids(available_tool_names: Iterable[str]) -> tuple[str, ...]:
    """可申请的目标包：模型可选、使用固定工具、且至少一个工具在本 Run 可用。

    mcp_explicit 需要选择别名，不在本期范围；完整可用性仍由首判骨架在重建时校验。
    """

    available = frozenset(available_tool_names)
    return tuple(
        package_id
        for package_id, spec in CAPABILITY_PACKAGES.items()
        if spec.model_selectable
        and spec.tools
        and not spec.mcp_aliases
        and package_id not in ESCALATION_SOURCE_PACKAGES
        and available.intersection(spec.tools)
    )


@dataclass(frozen=True)
class PendingEscalation:
    config: Any
    reason: str


@dataclass
class CapabilityEscalationSession:
    source_resolution: RunCapabilityResolution
    target_package_ids: tuple[str, ...]
    available_tool_names: tuple[str, ...]
    rebuild_call_config: Callable[[_CandidateRoute], Any]
    # lifecycle 在消息准备完成后挂上；为空时只切换执行面，不重组系统提示词。
    rebuild_system_messages: Callable[[Any], tuple[PromptMessage, ...]] | None = None
    system_section_ids: frozenset[str] = frozenset()
    # 返回拒绝原因码；execution 装配时按计划状态挂上。
    blocked_reason: Callable[[], str | None] | None = None
    attempts: int = 0
    pending: PendingEscalation | None = None
    applied: bool = False

    @property
    def closed(self) -> bool:
        return self.applied or self.pending is not None or self.attempts >= MAX_ESCALATION_REQUESTS_PER_RUN


def build_request_capability_schema(session: CapabilityEscalationSession) -> dict:
    package_lines = [render_runtime_prompt("capability_escalation.packages_description")]
    selectable_tools: list[str] = []
    for package_id in session.target_package_ids:
        spec = CAPABILITY_PACKAGES[package_id]
        tools = [name for name in spec.tools if name in session.available_tool_names]
        package_lines.append(f"- {package_id}: {', '.join(tools)}")
        selectable_tools.extend(name for name in tools if name not in selectable_tools)
    return {
        "type": "function",
        "function": {
            "name": REQUEST_CAPABILITY_TOOL_NAME,
            "description": render_runtime_prompt("capability_escalation.tool_description"),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "package_id": {
                        "type": "string",
                        "enum": list(session.target_package_ids),
                        "description": "\n".join(package_lines),
                    },
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string", "enum": selectable_tools},
                        "description": render_runtime_prompt("capability_escalation.tool_names_description"),
                    },
                    "primary_tool_name": {
                        "type": "string",
                        "enum": selectable_tools,
                        "description": render_runtime_prompt("capability_escalation.primary_tool_name_description"),
                    },
                    "reason": {
                        "type": "string",
                        "description": render_runtime_prompt("capability_escalation.reason_description"),
                    },
                },
                "required": ["package_id", "reason"],
            },
        },
    }


class RequestCapabilityHandler(BaseToolHandler):
    supports_automatic_retry = False

    def __init__(self, session: CapabilityEscalationSession) -> None:
        self._session = session

    @property
    def tool_name(self) -> str:
        return REQUEST_CAPABILITY_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return REQUEST_CAPABILITY_TOOL_NAME

    async def is_run_budget_exhausted(self) -> bool:
        return self._session.closed

    async def execute(self, args: dict) -> ToolResult:
        session = self._session
        args = args if isinstance(args, dict) else {}
        package_id = str(args.get("package_id") or "")
        if session.applied or session.pending is not None:
            return self._reject(package_id, "already_escalated")
        if session.attempts >= MAX_ESCALATION_REQUESTS_PER_RUN:
            return self._reject(package_id, "request_limit_reached")
        session.attempts += 1
        blocked = session.blocked_reason() if session.blocked_reason is not None else None
        if blocked:
            return self._reject(package_id, blocked)
        if package_id not in session.target_package_ids:
            return self._reject(package_id, "unknown_package")
        spec = CAPABILITY_PACKAGES[package_id]
        raw_tools = args.get("tool_names")
        tool_names = (
            tuple(name for name in raw_tools if isinstance(name, str))
            if isinstance(raw_tools, list) and raw_tools
            else spec.tools
        )
        primary = args.get("primary_tool_name")
        candidate = build_candidate_route(
            package_id=package_id,
            explicit_tool_names=tool_names,
            required_primary_tool_name=primary if isinstance(primary, str) and primary else None,
            network_policy="allow",
            denied_tool_names=(),
            output_mode=None,
            available_tools=list(session.available_tool_names),
            include_current_date=True,
        )
        if candidate is None:
            primary_name = primary if isinstance(primary, str) else None
            if spec.requires_primary_tool and primary_name not in tool_names:
                return self._reject(package_id, "primary_tool_name_required")
            return self._reject(package_id, "invalid_tool_selection")
        # 禁用与禁网约束只来自首判时用户的原话，模型的申请不能放宽它们。
        candidate = replace(
            candidate,
            denied_tool_names=tuple(sorted(session.source_resolution.denied_product_tool_names)),
        )
        try:
            config = session.rebuild_call_config(candidate)
        except ValueError:
            return self._reject(package_id, "capability_unavailable")
        resolution = getattr(config, "capability_resolution", None)
        if resolution is None or resolution.package_id != candidate.package_id:
            reason_codes = getattr(resolution, "reason_codes", ()) or ("capability_unavailable",)
            return self._reject(package_id, reason_codes[0])
        reason = str(args.get("reason") or "")[:_MAX_REASON_CHARS]
        session.pending = PendingEscalation(config=config, reason=reason)
        return ToolResult(
            status="success",
            data={
                "package_id": resolution.package_id,
                "tool_names": list(resolution.external_tool_names),
                "tool_signatures": tool_signatures(config.call_kwargs, resolution.external_tool_names),
                "reason": reason,
            },
        )

    def _reject(self, package_id: str, reason_code: str) -> ToolResult:
        result = _rejected(package_id, reason_code)
        result.data["can_retry"] = (
            reason_code in _RETRYABLE_REASON_CODES and self._session.attempts < MAX_ESCALATION_REQUESTS_PER_RUN
        )
        return result

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str):
        return None

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        data = result.data
        if result.status != "success":
            key = "rejected_retryable" if data.get("can_retry") else "rejected"
            return render_runtime_prompt(f"capability_escalation.{key}", reason=data.get("reason_code"))
        return render_runtime_prompt(
            "capability_escalation.granted",
            package_id=data["package_id"],
            # 本修复前落库的结果没有签名，重建历史上下文时退回工具名。
            tool_signatures="\n".join(
                f"- {line}" for line in data.get("tool_signatures") or data.get("tool_names") or ()
            ),
        )

    def _build_result_summary(self, result: ToolResult) -> dict:
        data = result.data
        return {
            "kind": REQUEST_CAPABILITY_TOOL_NAME,
            "truncated": False,
            "package_id": data.get("package_id"),
            "reason_code": data.get("reason_code"),
        }


def tool_signatures(call_kwargs: dict, tool_names: Iterable[str]) -> list[str]:
    """从本次真正下发的 schema 机械生成参数签名，放进批准结果。

    新工具在 Run 中途才出现在工具列表里，部分模型不会读取中途出现的定义而空参调用；
    把参数名、类型、枚举与必填放进对话正文，不依赖模型去重读工具列表。
    """

    schemas = {
        tool["function"]["name"]: tool["function"].get("parameters") or {}
        for tool in call_kwargs.get("tools") or []
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }
    return [f"{name}({_parameter_signature(schemas.get(name) or {})})" for name in tool_names]


def _parameter_signature(parameters: dict) -> str:
    required = set(parameters.get("required") or ())
    parts = []
    for field, spec in (parameters.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        part = f"{field}: {_type_label(spec)}"
        if field in required:
            part += ", required"
        parts.append(part)
    return "; ".join(parts)


def _type_label(spec: dict) -> str:
    if spec.get("enum"):
        return "one of " + "|".join(str(value) for value in spec["enum"])
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = "|".join(str(item) for item in kind)
    if kind == "array":
        items = spec.get("items") if isinstance(spec.get("items"), dict) else {}
        return f"array of {_type_label(items)}"
    return str(kind or "any")


def _rejected(package_id: str, reason_code: str) -> ToolResult:
    message = f"Capability request rejected: {reason_code}"
    return ToolResult(
        status="failed",
        data={"package_id": package_id or None, "reason_code": reason_code, "message": message},
        error_message=message,
    )


async def apply_pending_escalation(
    *,
    messages: list[PromptMessage],
    state: AgentLoopState,
    runtime: AgentLoopRuntime,
) -> AgentLoopRuntime:
    """在轮次边界把已批准的升级原子地切换到执行面、计划、网络预算与系统提示词。"""

    session = runtime.capability_escalation
    if session is None or session.pending is None:
        return runtime
    pending = session.pending
    session.pending = None
    session.applied = True
    config = pending.config

    # lifecycle 依赖本模块所在的 request_prep 链，延迟导入避免循环。
    from app.services.stream.agent_loop_lifecycle import capability_resolution_trajectory_payload

    if session.rebuild_system_messages is not None:
        new_system_messages = session.rebuild_system_messages(config)
        _replace_run_system_messages(
            messages,
            old_section_ids=session.system_section_ids,
            new_messages=new_system_messages,
        )
        session.system_section_ids = frozenset(message.section_id for message in new_system_messages)
    state.plan_coordinator = PlanCoordinator(
        run_id=runtime.run_id,
        mode=config.plan_mode,
        allowed_tool_names=frozenset(config.announced_tools),
        required_initial_tool_counts=dict(config.required_initial_tool_counts),
        unplanned_tool_names=frozenset(config.unplanned_tool_names),
    )
    runtime.network_budget.require_distinct_read_urls = "verified_research_request" in set(
        (config.plan_tool_policy_reason or "").split("+")
    )
    resolution = config.capability_resolution
    # 申请发生在刚结束的那一步；state.step 是最近一次已开始的步数。
    step_number = max(1, state.step)
    await runtime.emitter.capability_escalated(
        step_number=step_number,
        from_package_id=session.source_resolution.package_id,
        capability_resolution=capability_resolution_trajectory_payload(config),
        section_ids=[message.section_id for message in messages if message.role == "system" and message.section_id],
        system_prompt_fingerprint=fingerprint_system_messages(to_provider_messages(messages)),
    )
    logger.info(
        "CAPABILITY_ESCALATED run_id=%s model_id=%s step=%s from=%s to=%s tools=%s",
        runtime.run_id,
        runtime.model_id,
        step_number,
        session.source_resolution.package_id,
        resolution.package_id,
        ",".join(resolution.external_tool_names),
    )
    return replace(
        runtime,
        call_kwargs=config.call_kwargs,
        dynamic_tool_handlers=config.dynamic_tool_handlers,
        plan_mode=config.plan_mode,
        control_tool_names=config.control_tool_names,
        output_tool_names=config.output_tool_names,
        skill_tool_names=config.skill_tool_names,
        escalation_tool_names=frozenset(),
        evidence_policy=config.evidence_policy,
        capability_resolution=resolution,
    )


def _replace_run_system_messages(
    messages: list[PromptMessage],
    *,
    old_section_ids: frozenset[str],
    new_messages: tuple[PromptMessage, ...],
) -> None:
    def is_old(message: PromptMessage) -> bool:
        return message.role == "system" and message.section_id in old_section_ids

    insert_at = next((index for index, message in enumerate(messages) if is_old(message)), 0)
    kept_before = [message for message in messages[:insert_at] if not is_old(message)]
    kept_after = [message for message in messages[insert_at:] if not is_old(message)]
    messages[:] = finalize_model_call_language_policy([*kept_before, *new_messages, *kept_after])
