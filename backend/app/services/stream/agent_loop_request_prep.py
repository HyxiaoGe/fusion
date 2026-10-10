"""Agent loop 请求进入 driver 前的输入准备。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

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
    CURRENT_DOCUMENTS,
    DEEP_RESEARCH_CONTRACT,
    DOCUMENT_OUTPUT_CONTRACT,
    NO_TOOL_NETWORK_BOUNDARY,
    NO_VISION_FILE_BOUNDARY,
    SKILLS_CATALOG,
    TOOL_SELECTION_POLICY,
    TOOL_USAGE_CONTRACT,
)
from app.ai.prompts.system_prompt import SystemPromptSection, assemble_system_prompt
from app.ai.tools import build_url_read_tool, build_web_search_tool
from app.core.logger import app_logger as logger
from app.core.prompt_snapshot import PromptBundleSnapshot, use_prompt_snapshot
from app.db.repositories import FileRepository
from app.services.agent.plan_coordinator import PlanMode
from app.services.chat.message_builder import (
    build_llm_messages,
    inject_file_content,
    is_image_file,
)
from app.services.chat.tool_transcript_store import ToolTranscriptHistory, load_tool_transcripts
from app.services.documents.agent_tools import DocumentToolSet, render_current_documents_context
from app.services.mcp.amap_product_tools import AMAP_PRODUCT_TOOL_NAMES
from app.services.mcp.flyai_travel_tools import FLYAI_TRAVEL_TOOL_NAMES
from app.services.mcp.tool_deferral import (  # noqa: F401 — 阈值常量供测试从本模块引用
    MAX_DIRECT_MCP_SCHEMA_CHARS,
    MAX_DIRECT_MCP_TOOLS,
    should_defer_mcp_tools,
)
from app.services.prompt_snapshot_service import freeze_runtime_prompt_bundle, with_call_config_prompt_snapshot
from app.services.stream.agent_task_policy import resolve_agent_task_policy
from app.services.stream.dynamic_tool_discovery import (
    TOOL_SEARCH_NAME,
    DynamicToolDiscoverySession,
    attach_session_runtime,
    build_discovery_entries,
    build_tool_search_schema,
)
from app.services.stream.persistence import preprocess_url_in_message
from app.services.stream.reasoning_policy import configure_reasoning_call_kwargs
from app.services.stream.run_capability_router import (
    RunCapabilityResolution,
    resolve_run_capability_route,
)
from app.services.stream.skill_loading import (
    LOAD_SKILL_TOOL_NAME,
    LoadSkillHandler,
    SkillSession,
    build_load_skill_schema,
    build_skill_session,
)
from app.services.weather import WEATHER_TOOL_NAMES
from app.utils.run_capability_contract import is_authorized_mcp_tool_alias

if TYPE_CHECKING:
    from app.services.knowledge.agent_tool import KnowledgeToolSet

VOLCENGINE_PROVIDERS = {"volcengine"}
MAX_CONTROLLED_OUTPUT_TOKENS = 4096
# 文档正文整篇放在一次工具调用参数里，供应商默认输出上限可能截断 JSON；仅文档交付时显式放宽。
DOCUMENT_OUTPUT_MAX_TOKENS = 16384


@dataclass(frozen=True)
class AgentLoopCallConfig:
    should_use_reasoning: bool
    supports_function_calling: bool
    call_kwargs: dict
    announced_tools: list[str]
    capability_resolution: RunCapabilityResolution | None
    supports_dynamic_tools: bool = False
    dynamic_tool_handlers: dict[str, Any] = field(default_factory=dict)
    tool_bindings: list[dict[str, Any]] = field(default_factory=list)
    plan_mode: PlanMode = "auto"
    control_tool_names: frozenset[str] = frozenset()
    task_mode: str = "standard"
    network_profile: str = "standard"
    evidence_policy: str = "standard"
    prompt_bundle_snapshot: PromptBundleSnapshot | None = None
    # MCP 工具过多时的按需加载目录；未触发时为 None。
    tool_discovery: Any = None
    # 交付形态工具（文档）：不属于能力包，不进计划绑定，计划收口阶段仍可调用。
    output_tool_names: frozenset[str] = frozenset()
    document_context: str | None = None
    # 按需加载的 Skill：目录进 system prompt，load_skill 同样不进计划绑定。
    skill_session: SkillSession | None = None

    @property
    def skill_tool_names(self) -> frozenset[str]:
        return frozenset({LOAD_SKILL_TOOL_NAME}) if self.skill_session is not None else frozenset()

    @property
    def unplanned_tool_names(self) -> frozenset[str]:
        return self.output_tool_names | self.skill_tool_names


def build_update_plan_tool() -> dict[str, Any]:
    """仅供 Agent Loop 控制面消费，不映射到任何外部 handler；计划只用于向用户展示进度。"""

    return {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": render_runtime_prompt("stream.update_plan_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "explanation": {"type": "string", "description": render_runtime_prompt("stream.plan_explanation")},
                    "plan": {
                        "type": "array",
                        "maxItems": 14,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": render_runtime_prompt("stream.plan_step_id")},
                                "step": {"type": "string", "description": render_runtime_prompt("stream.plan_step")},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": render_runtime_prompt("stream.plan_status"),
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["reasoning", "search", "read", "synthesis", "answer", "other"],
                                },
                                "planned_tools": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": render_runtime_prompt("stream.planned_tools_available"),
                                },
                            },
                            "required": ["step", "status"],
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
    tool_history: ToolTranscriptHistory = field(default_factory=ToolTranscriptHistory)


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
    prompt_bundle_snapshot: PromptBundleSnapshot | None = None,
    previous_run_id: str | None = None,
    document_tools: DocumentToolSet | None = None,
    knowledge_tools: KnowledgeToolSet | None = None,
    direct_tool_names: tuple[str, ...] = (),
) -> AgentLoopCallConfig:
    prompt_bundle_snapshot = prompt_bundle_snapshot or freeze_runtime_prompt_bundle()
    options = options or {}
    capabilities = capabilities or {}

    use_reasoning = options.get("use_reasoning")
    supports_thinking = bool(capabilities.get("deepThinking", False))
    should_use_reasoning = use_reasoning is True or (use_reasoning is None and supports_thinking)

    tools_disabled = options.get("disable_tools") is True
    task_policy = resolve_agent_task_policy(options=options, capabilities=capabilities)
    requested_plan_mode = task_policy.plan_mode
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
    # 加载器指定必须直接公告的工具（如高德额度用尽时的其他地图工具）不参与按需加载。
    direct_names = set(direct_tool_names)
    mcp_tools = [
        tool
        for name, tool in available_tools_by_name.items()
        if is_authorized_mcp_tool_alias(name) and name not in direct_names
    ]
    deferred_tool_names = (
        [_tool_definition_name(tool) for tool in mcp_tools]
        if task_policy.task_mode != "deep_research" and should_defer_mcp_tools(mcp_tools)
        else []
    )
    capability_resolution = resolve_run_capability_route(
        available_tool_names=[name for name in available_tools_by_name if name not in deferred_tool_names],
        deferred_tool_names=deferred_tool_names,
        requested_plan_mode=requested_plan_mode,
        task_policy=task_policy,
        capabilities=capabilities,
        tools_disabled=tools_disabled,
    )
    agent_mode = capability_resolution.package_id == "agent"
    external_tool_names = list(capability_resolution.external_tool_names)
    tools = [available_tools_by_name[name] for name in external_tool_names]
    plan_mode = capability_resolution.effective_plan_mode
    control_tool_names: set[str] = set()
    if plan_mode != "off":
        tools.append(build_update_plan_tool())
        control_tool_names.add("update_plan")
    active_handlers = {name: provided_handlers[name] for name in external_tool_names if name in provided_handlers}
    document_handlers: dict[str, Any] = {}
    document_context: str | None = None
    if agent_mode and document_tools is not None and supports_dynamic_tools:
        with use_prompt_snapshot(prompt_bundle_snapshot):
            tools.extend(document_tools.definitions_factory())
            document_context = render_current_documents_context(document_tools.existing_documents)
        document_handlers = dict(document_tools.handlers)
        call_kwargs.setdefault("max_tokens", DOCUMENT_OUTPUT_MAX_TOKENS)
    knowledge_handlers: dict[str, Any] = {}
    if agent_mode and knowledge_tools is not None and supports_dynamic_tools:
        with use_prompt_snapshot(prompt_bundle_snapshot):
            tools.extend(knowledge_tools.definitions_factory())
        knowledge_handlers = dict(knowledge_tools.handlers)
    # 深度研究有自己的取证与综合契约，不叠加 Skill 方法论。
    skill_session = build_skill_session(external_tool_names) if agent_mode and supports_dynamic_tools else None
    skill_handlers: dict[str, Any] = {}
    if skill_session is not None:
        with use_prompt_snapshot(prompt_bundle_snapshot):
            tools.append(build_load_skill_schema(skill_session))
        skill_handlers[LOAD_SKILL_TOOL_NAME] = LoadSkillHandler(skill_session)
    if tools:
        call_kwargs["tools"] = tools
        call_kwargs["tool_choice"] = "auto"
    effective_provider = "volcengine" if provider in volcengine_providers else provider
    call_kwargs = configure_reasoning_call_kwargs(
        call_kwargs,
        provider=effective_provider,
        should_use_reasoning=should_use_reasoning,
        thinking_switchable=supports_thinking and capabilities.get("thinkingSwitchable") is True,
    )

    active_handlers.update(document_handlers)
    active_handlers.update(knowledge_handlers)
    active_handlers.update(skill_handlers)
    bindings_by_alias = {
        str(binding.get("alias", "")): binding
        for binding in (tool_bindings or [])
        if isinstance(binding, dict) and binding.get("alias")
    }
    active_bindings = [bindings_by_alias[name] for name in external_tool_names if name in bindings_by_alias]
    tool_discovery: DynamicToolDiscoverySession | None = None
    if capability_resolution.deferred_tool_names:
        tool_discovery = DynamicToolDiscoverySession(
            authorized=build_discovery_entries(
                schemas_by_name=available_tools_by_name,
                handlers_by_name=provided_handlers,
                bindings=tool_bindings or [],
                authorized_names=capability_resolution.deferred_tool_names,
            )
        )
        call_kwargs["tools"] = [*call_kwargs.get("tools", []), build_tool_search_schema()]
        call_kwargs["tool_choice"] = "auto"
        attach_session_runtime(
            tool_discovery,
            call_kwargs=call_kwargs,
            handlers=active_handlers,
            bindings=active_bindings,
        )
        control_tool_names.add(TOOL_SEARCH_NAME)
    return AgentLoopCallConfig(
        should_use_reasoning=should_use_reasoning,
        supports_function_calling=supports_function_calling,
        call_kwargs=call_kwargs,
        announced_tools=external_tool_names,
        capability_resolution=capability_resolution,
        supports_dynamic_tools=bool(active_handlers),
        dynamic_tool_handlers=active_handlers,
        tool_bindings=active_bindings,
        plan_mode=plan_mode,
        control_tool_names=frozenset(control_tool_names),
        task_mode=task_policy.task_mode,
        network_profile=task_policy.network_profile,
        evidence_policy=task_policy.evidence_policy,
        prompt_bundle_snapshot=prompt_bundle_snapshot,
        tool_discovery=tool_discovery,
        output_tool_names=frozenset(document_handlers),
        document_context=document_context,
        skill_session=skill_session,
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
    # 本次请求不带工具定义时不回放工具协议消息（部分模型会返回空回答），历史只带问答文字。
    tool_history = (
        _load_tool_history(db, conversation_id, raw_messages)
        if call_config.supports_function_calling and call_config.call_kwargs.get("tools")
        else ToolTranscriptHistory()
    )
    history_kwargs = {"tool_transcripts": tool_history.transcripts} if tool_history.transcripts else {}
    messages = ensure_prompt_messages(
        await build_llm_messages_fn(
            raw_messages,
            has_vision,
            file_repo,
            None,
            include_base_system=False,
            user_id=user_id,
            conversation_id=conversation_id,
            **history_kwargs,
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

        messages, initial_content_blocks = await _prepare_url_context(
            messages=messages,
            original_message=original_message,
            call_config=call_config,
            preprocess_url_in_message_fn=preprocess_url_in_message_fn,
        )
    else:
        initial_content_blocks = []
        has_image_attachment = False

    def assemble(config: AgentLoopCallConfig):
        return assemble_system_prompt(
            user_system_prompt=user_system_prompt,
            include_current_date=True,
            sections=partial(
                _run_prompt_sections,
                config,
                no_vision_file_boundary=has_image_attachment and not has_vision,
                extra_system_prompts=tuple(extra_system_prompts or ()),
            ),
        )

    assembly = assemble(call_config)
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
        final_tool_names=list(call_config.announced_tools),
        tool_history=tool_history,
    )


def _load_tool_history(db, conversation_id: str | None, raw_messages: list) -> ToolTranscriptHistory:
    """读取需要回放的历史工具记录；读取失败时这些轮次只带问答文字。"""
    if not conversation_id:
        return ToolTranscriptHistory()
    assistant_ids = [str(message.id) for message in raw_messages if getattr(message, "role", None) == "assistant"]
    try:
        return load_tool_transcripts(db, conversation_id, assistant_ids)
    except Exception as error:
        logger.warning("历史工具记录读取失败: conv_id=%s, error_type=%s", conversation_id, type(error).__name__)
        return ToolTranscriptHistory()


def _run_prompt_sections(
    call_config: AgentLoopCallConfig,
    *,
    no_vision_file_boundary: bool,
    extra_system_prompts: tuple[str, ...],
):
    # 只在空消息集上选择可信模板，用户文本不会影响段落是否存在。
    if no_vision_file_boundary:
        yield SystemPromptSection(NO_VISION_FILE_BOUNDARY, get_no_vision_file_boundary_prompt())
    for section_id in extra_system_prompts:
        yield SystemPromptSection(section_id, call_config.prompt_bundle_snapshot.resolve(section_id)[0])
    resolution = call_config.capability_resolution
    if getattr(call_config, "tool_discovery", None) is not None:
        yield SystemPromptSection("deferred_tool_catalog", call_config.tool_discovery.catalog_prompt())
    if resolution.package_id == "agent":
        # 工具选择交给回答模型：何时直接回答、何时查、用户禁止的不用，都写在这里而不是服务端预判。
        yield SystemPromptSection(TOOL_SELECTION_POLICY, render_runtime_prompt("stream.tool_selection_policy"))
    if resolution.external_tool_names:
        yield SystemPromptSection("tool_failure_policy", render_runtime_prompt("stream.tool_failure_policy"))
    if "web_search" in resolution.external_tool_names:
        yield SystemPromptSection(TOOL_USAGE_CONTRACT, get_tool_usage_contract_prompt())
    if resolution.effective_plan_mode != "off":
        yield SystemPromptSection(
            AGENT_PLAN_CONTROL,
            get_agent_plan_control_prompt(),
        )
    skill_session = getattr(call_config, "skill_session", None)
    if skill_session is not None:
        yield SystemPromptSection(SKILLS_CATALOG, skill_session.catalog_prompt())
    if resolution.package_id == "deep_research":
        yield SystemPromptSection(DEEP_RESEARCH_CONTRACT, DEEP_RESEARCH_CONTRACT_PROMPT)
    if resolution.network_boundary_required:
        yield SystemPromptSection(
            NO_TOOL_NETWORK_BOUNDARY,
            get_no_tool_network_boundary_prompt(),
        )
    if getattr(call_config, "output_tool_names", frozenset()):
        yield SystemPromptSection(DOCUMENT_OUTPUT_CONTRACT, render_runtime_prompt("documents.output_contract"))
        document_context = getattr(call_config, "document_context", None)
        if document_context:
            yield SystemPromptSection(CURRENT_DOCUMENTS, document_context)


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
    if "url_read" not in call_config.announced_tools:
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
        content=get_agent_plan_control_prompt(),
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
        or WEATHER_TOOL_NAMES.intersection(announced_tools)
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
