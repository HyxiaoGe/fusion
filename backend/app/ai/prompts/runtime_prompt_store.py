"""Load model-visible prompt text from local Jinja2 template data."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.prompt_template_engine import render_prompt_template

RUNTIME_PROMPT_FILE = Path(__file__).with_name("runtime_prompts.toml")


@lru_cache(maxsize=1)
def _runtime_prompt_data() -> dict[str, Any]:
    with RUNTIME_PROMPT_FILE.open("rb") as file:
        return tomllib.load(file)


def get_runtime_prompt_source(key: str) -> str:
    value: Any = _runtime_prompt_data()
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"未知本地 Prompt: {key}")
        value = value[part]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"本地 Prompt 正文无效: {key}")
    return value


def render_runtime_prompt(key: str, **variables: object) -> str:
    return render_prompt_template(get_runtime_prompt_source(key), "jinja2", variables)
