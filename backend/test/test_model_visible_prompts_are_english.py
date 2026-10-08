"""所有服务端生成的模型指令必须使用英文。"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

from app.ai import tools
from app.ai.prompts import agent_loop
from app.ai.prompts.local_templates import CODE_DEFAULT_PROMPT_TEMPLATES
from app.ai.prompts.runtime_prompt_store import RUNTIME_PROMPT_FILE, render_runtime_prompt
from app.processor import file_processor
from app.services.knowledge import chat_grounding
from app.services.mcp import amap_product_tools, flyai_travel_tools
from app.services.mcp.tool_contract import build_agent_tool_definition
from app.services.stream import (
    agent_loop_request_prep,
    agent_loop_round_outcome,
    limit_summary,
)
from app.services.stream.safe_fallback_response import SUPPORTED_FALLBACK_LOCALES

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


def test_static_system_prompts_are_english():
    modules = (
        agent_loop,
        chat_grounding,
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


def test_tool_definitions_are_english():
    values = (
        tools.build_web_search_tool(),
        tools.build_url_read_tool(),
        agent_loop_request_prep.build_update_plan_tool(),
        amap_product_tools.AMAP_PRODUCT_DEFINITIONS,
        flyai_travel_tools.FLYAI_TRAVEL_DEFINITIONS,
    )
    for index, value in enumerate(values):
        _assert_schema_descriptions_are_english(value, path=f"tool_definition[{index}]")

    mcp_definition = build_agent_tool_definition(
        # 服务名是管理员配置的数据，会进入描述；这里只校验模板文本为英文。
        SimpleNamespace(id="server-1", name="Docs service", endpoint_url="https://mcp.context7.com/mcp"),
        {
            "name": "resolve-library-id",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    _assert_schema_descriptions_are_english(mcp_definition, path="mcp_tool_definition")
    assert "Docs service / resolve-library-id" in mcp_definition["function"]["description"]


def test_bundled_skill_files_are_english():
    skills_root = Path(__file__).resolve().parents[1] / "app" / "ai" / "skills"
    for path in sorted(skills_root.glob("*/SKILL.md")) + sorted(skills_root.glob("*/references/*")):
        _assert_english(path.read_text(encoding="utf-8"), path=str(path.relative_to(skills_root)))


def test_runtime_prompt_file_is_external_english_jinja2_data():
    source = RUNTIME_PROMPT_FILE.read_text(encoding="utf-8")

    # safe_fallback.responses 是直接呈现给用户的最终正文，必须覆盖用户语言，不属于模型指令；
    # 模型可见的语言选择指令在 safe_fallback.language_selector，仍受英文约束。
    data = tomllib.loads(source)
    responses = data.get("safe_fallback", {}).pop("responses", None)
    _assert_english(data, path="runtime_prompts.toml")

    # 原先整文件扫描顺带保证了兜底文案齐全；改为按段排除后在这里显式补回覆盖度。
    assert set(responses) == set(SUPPORTED_FALLBACK_LOCALES), "安全兜底文案缺少受支持语言"
    for locale, bodies in responses.items():
        assert set(bodies) == {"tool_failure", "no_evidence", "protocol_error"}, f"{locale} 兜底原因不全"
        for reason, body in bodies.items():
            assert body.strip(), f"{locale}.{reason} 兜底文案为空"

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
