"""所有服务端生成的模型指令必须使用英文。"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

from app.ai import tools
from app.ai.prompts import agent_loop, product_results
from app.ai.prompts.local_templates import CODE_DEFAULT_PROMPT_TEMPLATES
from app.ai.prompts.runtime_prompt_store import RUNTIME_PROMPT_FILE, render_runtime_prompt
from app.processor import file_processor
from app.services.agent.plan_coordinator import PlanCoordinator
from app.services.external import kimi_search_service
from app.services.knowledge import chat_grounding
from app.services.mcp import amap_product_tools, flyai_travel_tools
from app.services.mcp.tool_contract import build_agent_tool_definition
from app.services.source_candidate_ranker import (
    RankedSourceCandidate,
    SourceReadDecision,
    SourceSelectionPlan,
    format_source_selection_guidance,
)
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.stream import (
    agent_loop_request_prep,
    agent_loop_round_outcome,
    limit_summary,
    research_evidence,
    tool_round,
)
from app.services.stream.run_capability_model_classifier import _system_prompt

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _assert_english(value, *, path: str) -> None:
    if isinstance(value, str):
        assert not _CJK.search(value), f"模型提示词仍含中文: {path}"
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_english(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_english(item, path=f"{path}[{index}]")


def _assert_schema_descriptions_are_english(value, *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "description":
                _assert_english(item, path=f"{path}.description")
            else:
                _assert_schema_descriptions_are_english(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_schema_descriptions_are_english(item, path=f"{path}[{index}]")


def test_static_system_prompts_and_classifier_are_english():
    modules = (
        agent_loop,
        product_results,
        chat_grounding,
        kimi_search_service,
        flyai_travel_tools,
        agent_loop_round_outcome,
        limit_summary,
        file_processor,
    )
    for module in modules:
        for name, value in vars(module).items():
            if isinstance(value, str) and ("PROMPT" in name or "CONTRACT" in name or name == "SYSTEM_PROMPT"):
                _assert_english(value, path=f"{module.__name__}.{name}")
    _assert_english(CODE_DEFAULT_PROMPT_TEMPLATES, path="CODE_DEFAULT_PROMPT_TEMPLATES")
    _assert_english(_system_prompt(), path="run_capability_model_classifier._system_prompt")


def test_tool_definitions_are_english():
    values = (
        tools.build_web_search_tool(),
        tools.build_url_read_tool(),
        agent_loop_request_prep.build_update_plan_tool(["web_search"]),
        agent_loop_request_prep._with_plan_item_binding(tools.build_web_search_tool(), required=True),
        amap_product_tools.AMAP_PRODUCT_DEFINITIONS,
        flyai_travel_tools.FLYAI_TRAVEL_DEFINITIONS,
    )
    for index, value in enumerate(values):
        _assert_schema_descriptions_are_english(value, path=f"tool_definition[{index}]")

    mcp_definition = build_agent_tool_definition(
        SimpleNamespace(id="server-1", name="中文服务名", endpoint_url="https://mcp.context7.com/mcp"),
        {
            "name": "resolve-library-id",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    _assert_schema_descriptions_are_english(mcp_definition, path="mcp_tool_definition")


def test_dynamic_research_and_tool_context_instructions_are_english():
    stage_prompts = [
        research_evidence.build_deep_research_stage_prompt(stage, active_plan_item_ids=["step-1"])
        for stage in ("search", "read", "search_repair", "synthesis")
    ]
    stage_prompts.append(research_evidence.build_deep_research_stage_prompt("planning", plan_repair_tool="web_search"))
    tool_contexts = [
        limit_summary.SUMMARY_TOOL_PROTOCOL_RETRY_PROMPT,
        agent_loop_round_outcome.PLAN_REQUIRED_RETRY_PROMPT,
        agent_loop_round_outcome.PLAN_EXECUTION_REQUIRED_RETRY_PROMPT,
        tool_round._format_missing_tool_result_context(),
        tool_round._format_not_executed_tool_context(),
        tool_round._format_reused_tool_context(),
        tool_round._format_unavailable_tool_context(),
    ]
    source_context = format_untrusted_source_context(
        UntrustedSourceContext(
            source_id="S1",
            source_type="search",
            title="Example",
            url="https://example.com",
            content="Example content",
        ),
        max_chars=100,
    )
    candidate = RankedSourceCandidate(
        rank=1,
        title="Example",
        url="https://example.com",
        domain="example.com",
        query="example",
        tool_call_id="call-1",
        source_index=1,
        score=100,
        priority="high",
        reasons=("official source",),
    )
    source_guidance = format_source_selection_guidance(
        SourceSelectionPlan(
            total_source_count=1,
            unique_source_count=1,
            search_queries=("example",),
            candidates=(candidate,),
            recommended=(candidate,),
            low_priority=(),
            read_decisions=(SourceReadDecision(candidate, "recommend_read", "official_original"),),
            decision_summary={},
            recommended_read_limit=1,
            read_required=True,
            minimum_required_reads=1,
        )
    )
    _assert_english(
        stage_prompts + tool_contexts + [source_context, source_guidance],
        path="dynamic_instructions",
    )


def test_server_generated_plan_text_exposed_to_the_model_is_english():
    coordinator = PlanCoordinator(
        run_id="run-english-fallback",
        mode="on",
        required_initial_tool_counts={"web_search": 1, "url_read": 2},
    )

    result = coordinator.adopt_research_fallback()

    assert result.accepted
    _assert_english(coordinator.canonical_plan_for_model(), path="server_generated_plan")


def test_bundled_skill_instruction_is_english():
    skill_path = (
        Path(__file__).resolve().parents[1] / "app" / "ai" / "skills" / "verified-research" / "1.0.0" / "SKILL.md"
    )
    _assert_english(skill_path.read_text(encoding="utf-8"), path="verified-research/SKILL.md")


def test_runtime_prompt_file_is_external_english_jinja2_data():
    source = RUNTIME_PROMPT_FILE.read_text(encoding="utf-8")

    _assert_english(source, path="runtime_prompts.toml")
    assert "{{ current_date }}" in source
    assert "2026-09-07" in render_runtime_prompt(
        "agent_loop.current_date",
        current_date="2026-09-07",
        current_time="12:00",
        tomorrow="2026-09-08",
        this_saturday="2026-09-12",
        this_sunday="2026-09-13",
        next_saturday="2026-09-19",
        next_sunday="2026-09-20",
    )


def test_model_instruction_bodies_are_not_embedded_in_python_modules():
    app_root = Path(__file__).resolve().parents[1] / "app"
    instruction_cues = (
        "Do not ",
        "Never ",
        "You are ",
        "Use only ",
        "Call ",
        "Answer ",
        "Return only ",
        "When the user",
        "The following ",
        "Immediately ",
        "Mandatory ",
        "Required ",
    )

    embedded: list[str] = []
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if len(node.value) < 80 or not any(cue in node.value for cue in instruction_cues):
                continue
            embedded.append(f"{path.relative_to(app_root)}:{node.lineno}")

    assert embedded == [], f"模型指令正文仍内嵌于 Python: {embedded}"
