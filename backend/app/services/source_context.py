"""外部来源上下文格式化。"""

from __future__ import annotations

from typing import Literal
from xml.sax.saxutils import escape

from pydantic import BaseModel

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt


class UntrustedSourceContext(BaseModel):
    source_id: str
    source_type: Literal["search", "url_read"]
    title: str
    url: str
    content: str
    retrieved_at: str | None = None
    provider: str | None = None


def format_untrusted_source_context(context: UntrustedSourceContext, max_chars: int) -> str:
    content = context.content or ""
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    attrs = [
        f'source_id="{_escape_xml_attribute(context.source_id)}"',
        f'source_type="{_escape_xml_attribute(context.source_type)}"',
        f'source_url="{_escape_xml_attribute(context.url)}"',
    ]
    if context.provider:
        attrs.append(f'provider="{_escape_xml_attribute(context.provider)}"')

    return render_runtime_prompt(
        "source_context.web",
        attrs=" ".join(attrs),
        title=escape(context.title or "Unknown"),
        content=escape(content),
        truncated=truncated,
    )


def _escape_xml_attribute(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})
