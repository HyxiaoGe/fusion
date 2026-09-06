"""本地 Jinja2 Prompt 的变量与沙箱契约。"""

import pytest

from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
from app.core.prompt_catalog import PROMPT_SPECS
from app.core.prompt_template_engine import render_prompt_template, template_contract_is_valid


def test_all_local_templates_match_declared_jinja_variables():
    for spec in PROMPT_SPECS:
        assert template_contract_is_valid(
            DEFAULT_PROMPT_TEMPLATES[spec.key],
            spec.variables,
            spec.template_engine,
        )


def test_strict_undefined_sandbox_and_trailing_newline():
    assert render_prompt_template("{{ content }}\n", "jinja2", {"content": "<原样>"}) == "<原样>\n"
    with pytest.raises(ValueError):
        render_prompt_template("{{ content }}", "jinja2", {})
    with pytest.raises(ValueError):
        render_prompt_template("{{ content.__class__.__mro__ }}", "jinja2", {"content": "x"})
