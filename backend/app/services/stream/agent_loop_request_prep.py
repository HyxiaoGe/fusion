"""Agent loop 请求进入 driver 前的输入准备。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts.agent_loop import (
    DEEP_RESEARCH_CONTRACT_PROMPT,
    get_agent_plan_control_prompt,
    get_no_tool_network_boundary_prompt,
    get_no_vision_file_boundary_prompt,
    get_tool_usage_contract_prompt,
)
from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_message, ensure_prompt_messages
from app.ai.prompts.run_prompt_snapshot import RunPromptSnapshot
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.ai.prompts.section_ids import (
    AGENT_PLAN_CONTROL,
    CONTINUATION_SYSTEM,
    DEEP_RESEARCH_CONTRACT,
    NO_TOOL_NETWORK_BOUNDARY,
    NO_VISION_FILE_BOUNDARY,
    TOOL_USAGE_CONTRACT,
)
from app.ai.prompts.system_prompt import SystemPromptSection, assemble_system_prompt
from app.ai.skills.registry import RunSkillResolution, SkillReleasePin, load_skills_for_package
from app.ai.tools import build_url_read_tool, build_web_search_tool
from app.core.prompt_snapshot import PromptBundleSnapshot, use_prompt_snapshot
from app.db.repositories import FileRepository
from app.services.agent.plan_coordinator import PlanMode
from app.services.chat.message_builder import (
    build_llm_messages,
    inject_file_content,
    is_image_file,
)
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_TOOL_NAMES
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_TOOL_NAMES
from app.services.prompt_snapshot_service import freeze_runtime_prompt_bundle, with_call_config_prompt_snapshot
from app.services.stream.agent_plan_tool_policy import (
    AgentPlanToolPolicy,
    resolve_agent_plan_tool_policy,
    resolve_product_package_plan_policy,
)
from app.services.stream.agent_task_policy import resolve_agent_task_policy
from app.services.stream.persistence import preprocess_url_in_message
from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs
from app.services.stream.run_capability_router import (
    CapabilityClassifier,
    RunCapabilityResolution,
    resolve_run_capability_route,
)

VOLCENGINE_PROVIDERS = {"volcengine"}
MAX_CONTROLLED_OUTPUT_TOKENS = 4096
PLAN_ITEM_ARGUMENT_NAME = "_plan_item_id"
PLAN_ITEM_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


@dataclass(frozen=True)
class AgentLoopCallConfig:
    should_use_reasoning: bool
    supports_function_calling: bool
    call_kwargs: dict
    announced_tools: list[str]
    capability_resolution: RunCapabilityResolution
    supports_dynamic_tools: bool = False
    dynamic_tool_handlers: dict[str, Any] = field(default_factory=dict)
    tool_bindings: list[dict[str, Any]] = field(default_factory=list)
    plan_mode: PlanMode = "auto"
    control_tool_names: frozenset[str] = frozenset()
    task_mode: str = "standard"
    network_profile: str = "standard"
    evidence_policy: str = "standard"
    required_initial_tool_counts: dict[str, int] = field(default_factory=dict)
    plan_tool_policy_reason: str | None = None
    prompt_bundle_snapshot: PromptBundleSnapshot | None = None


def build_update_plan_tool(allowed_tool_names: list[str] | None = None) -> dict[str, Any]:
    """仅供 Agent Loop 控制面消费，不映射到任何外部 handler。"""

    planned_tool_schema: dict[str, Any] = {"type": "string"}
    normalized_allowed_tool_names = list(dict.fromkeys(allowed_tool_names or []))
    if normalized_allowed_tool_names:
        planned_tool_schema["enum"] = normalized_allowed_tool_names
    planned_tools_max_items = 1 if normalized_allowed_tool_names else 0
    planned_tools_description = render_runtime_prompt(
        "stream.planned_tools_available" if normalized_allowed_tool_names else "stream.planned_tools_unavailable"
    )

    return {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": render_runtime_prompt("stream.update_plan_description"),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "explanation": {"type": "string", "description": render_runtime_prompt("stream.plan_explanation")},
                    "plan": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "pattern": PLAN_ITEM_ID_PATTERN,
                                    "description": render_runtime_prompt("stream.plan_step_id"),
                                },
                                "step": {"type": "string", "description": render_runtime_prompt("stream.plan_step")},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress"],
                                    "description": render_runtime_prompt("stream.plan_status"),
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["reasoning", "search", "read", "synthesis", "answer", "other"],
                                },
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "planned_tools": {
                                    "type": "array",
                                    "items": planned_tool_schema,
                                    "maxItems": planned_tools_max_items,
                                    "description": planned_tools_description,
                                },
                            },
                            "required": ["id", "step", "status", "planned_tools"],
                        },
                    },
                },
                "required": ["plan"],
            },
        },
    }


@dataclass(frozen=True)
class AgentLoopPreparedMessages:
    messages: list[PromptMessage]
    initial_content_blocks: list[Any] = field(default_factory=list)
    final_tool_names: list[str] = field(default_factory=list)
    prompt_assembly: dict[str, Any] | None = None
    prompt_snapshot: dict[str, Any] | None = None
    run_prompt_snapshot: RunPromptSnapshot | None = None


def announced_tool_names_from_call_kwargs(call_kwargs: dict) -> list[str]:
    ordered_names: list[str] = []
    for tool in call_kwargs.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            ordered_names.append(str(fn["name"]))
    return ordered_names


def _tool_definition_name(tool: dict) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _with_plan_item_binding(tool: dict, *, required: bool) -> dict:
    """给外部工具增加仅供 Agent Loop 消费的计划项关联字段。"""

    if _tool_definition_name(tool) == "update_plan":
        return tool
    prepared = deepcopy(tool)
    function = prepared.get("function")
    if not isinstance(function, dict):
        return prepared
    parameters = function.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return prepared
    properties = parameters.setdefault("properties", {})
    if not isinstance(properties, dict):
        return prepared
    properties[PLAN_ITEM_ARGUMENT_NAME] = {
        "type": "string",
        "pattern": PLAN_ITEM_ID_PATTERN,
        "description": render_runtime_prompt("stream.plan_item_binding"),
    }
    if required:
        required_fields = parameters.setdefault("required", [])
        if isinstance(required_fields, list) and PLAN_ITEM_ARGUMENT_NAME not in required_fields:
            required_fields.append(PLAN_ITEM_ARGUMENT_NAME)
    return prepared


def supports_search_tools(capabilities: dict) -> bool:
    function_calling = bool(capabilities.get("functionCalling", False))
    if "searchCapable" in capabilities:
        return function_calling and bool(capabilities.get("searchCapable"))
    return function_calling and bool(capabilities.get("agentTools", capabilities.get("functionCalling", False)))


def supports_dynamic_agent_tools(capabilities: dict) -> bool:
    """显式拒绝 agentTools 的模型不得绕过兼容性门禁加载动态工具。"""
    return bool(capabilities.get("functionCalling", False)) and bool(
        capabilities.get("agentTools", capabilities.get("functionCalling", False))
    )


def normalize_controlled_max_tokens(value: Any) -> int | None:
    """只接受受控的正整数输出上限，拒绝 bool 与隐式类型转换。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return min(value, MAX_CONTROLLED_OUTPUT_TOKENS)


def build_agent_loop_call_config(
    *,
    provider: str,
    options: dict | None,
    capabilities: dict | None,
    volcengine_providers: set[str] | frozenset[str] = frozenset(VOLCENGINE_PROVIDERS),
    build_web_search_tool_fn: Callable[[], dict] = build_web_search_tool,
    build_url_read_tool_fn: Callable[[], dict] = build_url_read_tool,
    additional_tools: list[dict] | None = None,
    dynamic_tool_handlers: dict[str, Any] | None = None,
    tool_bindings: list[dict[str, Any]] | None = None,
    authorized_tool_names: list[str] | None = None,
    original_message: str | None = None,
    task_context_messages: list[object] | None = None,
    skill_release_pins: tuple[SkillReleasePin, ...] | None = None,
    classify_fn: CapabilityClassifier | None = None,
    prompt_bundle_snapshot: PromptBundleSnapshot | None = None,
) -> AgentLoopCallConfig:
    prompt_bundle_snapshot = prompt_bundle_snapshot or freeze_runtime_prompt_bundle()
    options = options or {}
    capabilities = capabilities or {}
    knowledge_grounded = options.get("knowledge_grounded") is True

    use_reasoning = options.get("use_reasoning")
    supports_thinking = bool(capabilities.get("deepThinking", False))
    should_use_reasoning = use_reasoning is True or (use_reasoning is None and supports_thinking)

    tools_disabled = options.get("disable_tools") is True or knowledge_grounded
    task_policy = resolve_agent_task_policy(options=options, capabilities=capabilities)
    requested_plan_mode = "off" if knowledge_grounded else task_policy.plan_mode
    supports_function_calling = supports_search_tools(capabilities) and not tools_disabled
    supports_dynamic_tools = supports_dynamic_agent_tools(capabilities) and not tools_disabled
    call_kwargs: dict = {}
    max_tokens = normalize_controlled_max_tokens(options.get("max_tokens"))
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    available_tools: list[dict] = []
    if supports_function_calling:
        with use_prompt_snapshot(prompt_bundle_snapshot):
            available_tools.extend([build_web_search_tool_fn(), build_url_read_tool_fn()])
    provided_handlers = dynamic_tool_handlers or {}
    if supports_dynamic_tools:
        available_tools.extend(
            tool for tool in (additional_tools or []) if _tool_definition_name(tool) in provided_handlers
        )
    available_tools_by_name = {name: tool for tool in available_tools if (name := _tool_definition_name(tool))}
    trusted_authorized_tool_names = [name for name in (authorized_tool_names or []) if isinstance(name, str) and name]
    route_tool_names = list(available_tools_by_name)
    route_tool_names.extend(
        name for name in dict.fromkeys(trusted_authorized_tool_names) if name not in available_tools_by_name
    )
    unavailable_tool_names = [name for name in trusted_authorized_tool_names if name not in available_tools_by_name]
    skill_loader = None
    if skill_release_pins is not None:

        def skill_loader(package_id, routed_tool_names):
            return load_skills_for_package(
                package_id,
                routed_tool_names,
                release_pins=skill_release_pins,
            )

    with use_prompt_snapshot(prompt_bundle_snapshot):
        capability_resolution = resolve_run_capability_route(
            original_message=original_message,
            task_context_messages=task_context_messages,
            available_tool_names=route_tool_names,
            requested_plan_mode=requested_plan_mode,
            task_policy=task_policy,
            capabilities=capabilities,
            tools_disabled=tools_disabled,
            knowledge_grounded=knowledge_grounded,
            unavailable_tool_names=unavailable_tool_names,
            load_skills_fn=skill_loader,
            classify_fn=classify_fn,
        )
    if (
        skill_release_pins
        and capability_resolution.skill_resolution is not None
        and capability_resolution.skill_resolution.status == "not_selected"
    ):
        capability_resolution = RunCapabilityResolution(
            schema_version=capability_resolution.schema_version,
            router_version=capability_resolution.router_version,
            package_id="tools_unavailable",
            confidence=capability_resolution.confidence,
            resolution_mode="degraded",
            reason_codes=("required_skill_unavailable",),
            external_tool_names=(),
            effective_plan_mode="off",
            include_current_date=capability_resolution.include_current_date,
            network_boundary_required=True,
            skill_resolution=RunSkillResolution(
                status="load_failed",
                activation_source="capability_package",
                requested_skill_ids=tuple(pin.skill_id for pin in skill_release_pins),
                skills=(),
                duration_ms=0,
                error_code="skill_load_failed",
            ),
            loaded_skills=(),
        )
    external_tool_names = list(capability_resolution.external_tool_names)
    tools = [available_tools_by_name[name] for name in external_tool_names]
    plan_mode = capability_resolution.effective_plan_mode
    if capability_resolution.package_id == "deep_research":
        schedulable_names = frozenset({"web_search", "url_read"}).intersection(external_tool_names)
        plan_tool_policy = AgentPlanToolPolicy(
            allowed_tool_names=frozenset(schedulable_names),
            reason="deep_research_schedulable_tools",
        )
    elif capability_resolution.package_id == "verified_web" and plan_mode != "off":
        plan_tool_policy = AgentPlanToolPolicy(
            required_initial_tool_counts={"web_search": 1, "url_read": 2},
            reason="verified_research_request",
        )
    elif capability_resolution.package_id == "verified_web":
        plan_tool_policy = AgentPlanToolPolicy()
    else:
        # 产品包直接消费已冻结的分类结果，不再用正则从原文二次推导出行意图（issue #30）。
        plan_tool_policy = resolve_product_package_plan_policy(
            package_id=capability_resolution.package_id,
            announced_tool_names=external_tool_names,
        ) or resolve_agent_plan_tool_policy(
            original_message=original_message,
            announced_tool_names=external_tool_names,
            task_context_messages=task_context_messages,
        )
    control_tool_names: frozenset[str] = frozenset()
    if plan_mode != "off":
        tools.append(build_update_plan_tool(external_tool_names))
        control_tool_names = frozenset({"update_plan"})
        tools = [
            _with_plan_item_binding(tool, required=plan_mode == "on")
            if _tool_definition_name(tool) not in control_tool_names
            else tool
            for tool in tools
        ]
    if tools:
        call_kwargs["tools"] = tools
        call_kwargs["tool_choice"] = "auto"
    effective_provider = "volcengine" if provider in volcengine_providers else provider
    call_kwargs = configure_reasoning_call_kwargs(
        call_kwargs,
        provider=effective_provider,
        should_use_reasoning=should_use_reasoning,
    )

    announced_tools = list(external_tool_names)
    active_handlers = {name: provided_handlers[name] for name in announced_tools if name in provided_handlers}
    bindings_by_alias = {
        str(binding.get("alias", "")): binding
        for binding in (tool_bindings or [])
        if isinstance(binding, dict) and binding.get("alias")
    }
    active_bindings = [bindings_by_alias[name] for name in announced_tools if name in bindings_by_alias]
    return AgentLoopCallConfig(
        should_use_reasoning=should_use_reasoning,
        supports_function_calling=supports_function_calling,
        call_kwargs=call_kwargs,
        announced_tools=announced_tools,
        capability_resolution=capability_resolution,
        supports_dynamic_tools=bool(active_handlers),
        dynamic_tool_handlers=active_handlers,
        tool_bindings=active_bindings,
        plan_mode=plan_mode,
        control_tool_names=control_tool_names,
        task_mode=task_policy.task_mode,
        network_profile=task_policy.network_profile,
        evidence_policy="knowledge_grounded_v1" if knowledge_grounded else task_policy.evidence_policy,
        required_initial_tool_counts=dict(plan_tool_policy.required_initial_tool_counts),
        plan_tool_policy_reason=plan_tool_policy.reason,
        prompt_bundle_snapshot=prompt_bundle_snapshot,
    )


def load_user_system_prompt(db, user_id: str) -> str | None:
    from app.db.models import User as UserModel

    user_record = db.query(UserModel).filter(UserModel.id == user_id).first()
    return user_record.system_prompt if user_record else None


@with_call_config_prompt_snapshot
async def prepare_agent_loop_messages(
    *,
    db,
    user_id: str,
    conversation_id: str | None = None,
    raw_messages: list,
    has_vision: bool,
    file_ids: list | None,
    original_message: str,
    call_config: AgentLoopCallConfig,
    file_repo_factory: Callable[[Any], Any] | None = None,
    load_user_system_prompt_fn: Callable[[Any, str], str | None] | None = None,
    build_llm_messages_fn: Callable[..., Awaitable[list[PromptMessage | dict]]] | None = None,
    is_image_file_fn: Callable[[str, Any], bool] | None = None,
    inject_file_content_fn: Callable[
        [list[PromptMessage | dict], str, dict[str, str]],
        list[PromptMessage | dict],
    ]
    | None = None,
    preprocess_url_in_message_fn: Callable[..., Awaitable[tuple[Any | None, dict | None, str | None]]] | None = None,
    preprocess_user_input: bool = True,
    extra_system_prompts: list[str] | None = None,
) -> AgentLoopPreparedMessages:
    if any(section_id != CONTINUATION_SYSTEM for section_id in extra_system_prompts or []):
        raise ValueError("extra_system_prompts 只能包含已注册的 section identity")
    file_repo_factory = file_repo_factory or FileRepository
    load_user_system_prompt_fn = load_user_system_prompt_fn or load_user_system_prompt
    build_llm_messages_fn = build_llm_messages_fn or build_llm_messages
    is_image_file_fn = is_image_file_fn or is_image_file
    inject_file_content_fn = inject_file_content_fn or inject_file_content
    preprocess_url_in_message_fn = preprocess_url_in_message_fn or preprocess_url_in_message

    file_repo = file_repo_factory(db)
    user_system_prompt = load_user_system_prompt_fn(db, user_id)
    messages = ensure_prompt_messages(
        await build_llm_messages_fn(
            raw_messages,
            has_vision,
            file_repo,
            None,
            include_base_system=False,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    )

    if preprocess_user_input:
        has_image_attachment = _has_image_file(
            file_ids=file_ids,
            file_repo=file_repo,
            is_image_file_fn=is_image_file_fn,
        )
        messages = ensure_prompt_messages(
            _inject_non_image_file_contents(
                messages=messages,
                file_ids=file_ids,
                original_message=original_message,
                file_repo=file_repo,
                is_image_file_fn=is_image_file_fn,
                inject_file_content_fn=inject_file_content_fn,
            )
        )

        if call_config.evidence_policy == "knowledge_grounded_v1":
            initial_content_blocks = []
        else:
            messages, initial_content_blocks = await _prepare_url_context(
                messages=messages,
                original_message=original_message,
                call_config=call_config,
                preprocess_url_in_message_fn=preprocess_url_in_message_fn,
            )
    else:
        initial_content_blocks = []
        has_image_attachment = False

    def selected_sections():
        # 只在空消息集上选择可信模板，用户文本不会影响段落是否存在。
        if has_image_attachment and not has_vision:
            yield SystemPromptSection(NO_VISION_FILE_BOUNDARY, get_no_vision_file_boundary_prompt())
        for section_id in extra_system_prompts or []:
            yield SystemPromptSection(section_id, call_config.prompt_bundle_snapshot.resolve(section_id)[0])
        resolution = call_config.capability_resolution
        if "web_search" in resolution.external_tool_names:
            yield SystemPromptSection(TOOL_USAGE_CONTRACT, get_tool_usage_contract_prompt())
        if resolution.effective_plan_mode != "off":
            yield SystemPromptSection(
                AGENT_PLAN_CONTROL,
                get_agent_plan_control_prompt(resolution.effective_plan_mode),
            )
        for skill in resolution.loaded_skills:
            yield SystemPromptSection(skill.metadata.section_id, skill.content)
        if resolution.package_id == "deep_research":
            yield SystemPromptSection(DEEP_RESEARCH_CONTRACT, DEEP_RESEARCH_CONTRACT_PROMPT)
        if resolution.network_boundary_required:
            yield SystemPromptSection(
                NO_TOOL_NETWORK_BOUNDARY,
                get_no_tool_network_boundary_prompt(),
            )

    assembly = assemble_system_prompt(
        user_system_prompt=user_system_prompt,
        include_current_date=call_config.capability_resolution.include_current_date,
        sections=selected_sections,
    )
    messages = [*assembly.messages, *ensure_prompt_messages(messages)]
    run_snapshot = RunPromptSnapshot(
        bundle_snapshot=call_config.prompt_bundle_snapshot,
        messages=tuple(assembly.messages),
        template_version=assembly.metadata["template_version"],
    )
    return AgentLoopPreparedMessages(
        messages=messages,
        initial_content_blocks=initial_content_blocks,
        prompt_assembly=assembly.metadata,
        prompt_snapshot=run_snapshot.to_storage(),
        run_prompt_snapshot=run_snapshot,
        final_tool_names=list(call_config.capability_resolution.external_tool_names),
    )


def inject_extra_system_prompts(
    messages: list[PromptMessage | dict],
    prompts: list[str],
) -> list[PromptMessage]:
    messages[:] = ensure_prompt_messages(messages)
    if not prompts:
        return messages

    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    prompt_messages = [
        PromptMessage(role="system", content=prompt, section_id=f"extra_system_{index}")
        for index, prompt in enumerate(prompts)
    ]
    messages[insert_at:insert_at] = prompt_messages
    return messages


def _has_image_file(
    *,
    file_ids: list | None,
    file_repo: Any,
    is_image_file_fn: Callable[[str, Any], bool],
) -> bool:
    if not file_ids:
        return False
    return any(is_image_file_fn(fid, file_repo) for fid in file_ids)


def _inject_non_image_file_contents(
    *,
    messages: list[PromptMessage],
    file_ids: list | None,
    original_message: str,
    file_repo: Any,
    is_image_file_fn: Callable[[str, Any], bool],
    inject_file_content_fn: Callable[
        [list[PromptMessage | dict], str, dict[str, str]],
        list[PromptMessage | dict],
    ],
) -> list[PromptMessage | dict]:
    if not file_ids:
        return messages

    non_image_ids = [fid for fid in file_ids if not is_image_file_fn(fid, file_repo)]
    if not non_image_ids:
        return messages

    file_contents = file_repo.get_parsed_file_content(non_image_ids)
    if not file_contents:
        return messages
    return inject_file_content_fn(messages, original_message, file_contents)


async def _prepare_url_context(
    *,
    messages: list[PromptMessage],
    original_message: str,
    call_config: AgentLoopCallConfig,
    preprocess_url_in_message_fn: Callable[..., Awaitable[tuple[Any | None, dict | None, str | None]]],
) -> tuple[list[PromptMessage], list[Any]]:
    if "url_read" not in call_config.capability_resolution.external_tool_names:
        return messages, []
    initial_content_blocks = []
    url_read_block, url_context_msg, _auto_detected_url = await preprocess_url_in_message_fn(
        original_message,
        call_config.supports_function_calling,
        call_config.call_kwargs,
    )
    if url_context_msg:
        messages.insert(-1, ensure_prompt_message(url_context_msg))
    if url_read_block:
        initial_content_blocks.append(url_read_block)
    return messages, initial_content_blocks


def inject_tool_usage_contract(
    messages: list[PromptMessage | dict],
    call_kwargs: dict,
) -> list[PromptMessage]:
    """工具模式下补一条 system 约束，避免 reasoning 口头承诺搜索但不发 tool_call。"""
    messages[:] = ensure_prompt_messages(messages)
    if "web_search" not in set(announced_tool_names_from_call_kwargs(call_kwargs)):
        return messages
    if any(message.section_id == TOOL_USAGE_CONTRACT for message in messages):
        return messages

    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    contract_msg = PromptMessage(
        role="system",
        content=get_tool_usage_contract_prompt(),
        section_id=TOOL_USAGE_CONTRACT,
    )
    messages.insert(insert_at, contract_msg)
    return messages


def inject_plan_control_contract(
    messages: list[PromptMessage | dict],
    call_config: AgentLoopCallConfig,
) -> list[PromptMessage]:
    """向支持计划控制的模型注入行为契约，避免复杂任务只展示观察型占位计划。"""

    messages[:] = ensure_prompt_messages(messages)
    if "update_plan" not in getattr(call_config, "control_tool_names", frozenset()):
        return messages
    if any(message.section_id == AGENT_PLAN_CONTROL for message in messages):
        return messages

    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    contract_msg = PromptMessage(
        role="system",
        content=get_agent_plan_control_prompt(call_config.plan_mode),
        section_id=AGENT_PLAN_CONTROL,
    )
    messages.insert(insert_at, contract_msg)
    return messages


def inject_deep_research_contract(
    messages: list[PromptMessage | dict],
    call_config: AgentLoopCallConfig,
) -> list[PromptMessage]:
    messages[:] = ensure_prompt_messages(messages)
    if getattr(call_config, "task_mode", "standard") != "deep_research":
        return messages
    if any(message.section_id == DEEP_RESEARCH_CONTRACT for message in messages):
        return messages
    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    messages.insert(
        insert_at,
        PromptMessage(
            role="system",
            content=DEEP_RESEARCH_CONTRACT_PROMPT,
            section_id=DEEP_RESEARCH_CONTRACT,
        ),
    )
    return messages


def inject_no_tool_network_boundary(
    messages: list[PromptMessage | dict],
    call_kwargs: dict,
) -> list[PromptMessage]:
    """无联网工具模式下补一条 system 边界，避免模型把内部知识包装成实时搜索。"""
    messages[:] = ensure_prompt_messages(messages)
    announced_tools = set(announced_tool_names_from_call_kwargs(call_kwargs))
    network_tool_names = {"web_search", "url_read"}
    if (
        network_tool_names.intersection(announced_tools)
        or AMAP_PRODUCT_TOOL_NAMES.intersection(announced_tools)
        or FLYAI_TRAVEL_TOOL_NAMES.intersection(announced_tools)
        or any(name.startswith("mcp_") for name in announced_tools)
    ):
        return messages
    if any(message.section_id == NO_TOOL_NETWORK_BOUNDARY for message in messages):
        return messages

    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    boundary_msg = PromptMessage(
        role="system",
        content=get_no_tool_network_boundary_prompt(),
        section_id=NO_TOOL_NETWORK_BOUNDARY,
    )
    messages.insert(insert_at, boundary_msg)
    return messages


def inject_no_vision_file_boundary(messages: list[PromptMessage | dict]) -> list[PromptMessage]:
    """图片已附加但当前模型无 vision 时，给 LLM 明确能力边界，避免臆测图片内容。"""
    messages[:] = ensure_prompt_messages(messages)
    if any(message.section_id == NO_VISION_FILE_BOUNDARY for message in messages):
        return messages

    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == "system":
        insert_at += 1
    boundary_msg = PromptMessage(
        role="system",
        content=get_no_vision_file_boundary_prompt(),
        section_id=NO_VISION_FILE_BOUNDARY,
    )
    messages.insert(insert_at, boundary_msg)
    return messages
