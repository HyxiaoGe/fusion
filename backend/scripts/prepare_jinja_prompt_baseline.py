"""从已导出的 published 原始响应生成待审阅基线；不写 PromptHub 或数据库。"""

import argparse
import copy
import hashlib
import json
import os
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinja2 import StrictUndefined  # noqa: E402
from jinja2.sandbox import SandboxedEnvironment  # noqa: E402

from app.core.prompt_bundle import validate_published_bundle  # noqa: E402
from app.core.prompt_template_engine import payload_template_engine, template_contract_is_valid  # noqa: E402
from app.services.external.prompthub_client import _parse_bundle_envelope  # noqa: E402
from app.services.prompt_effective_map import assert_p0_transition_gate, bundle_payload_contents  # noqa: E402


def convert(content, variables):
    if not variables:
        return content
    parts = []
    for literal, name, format_spec, conversion in string.Formatter().parse(content):
        if format_spec or conversion or any(marker in literal for marker in ("{{", "{%", "{#")):
            raise ValueError("复杂格式或 Jinja 字面语法需要单独审阅，不能自动转换")
        parts.append(literal)
        if name is not None:
            if name not in variables:
                raise ValueError("占位符与原始声明不一致")
            parts.append("{{ " + name + " }}")
    return "".join(parts)


def prepare(bundle):
    payload = validate_published_bundle(bundle)
    if payload_template_engine(payload) != "none":
        raise ValueError("迁移提案必须来自完整旧引擎 v2")
    assert_p0_transition_gate(bundle_payload_contents(payload), template_engine="none")
    result = []
    reference = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)
    for item in sorted(bundle.prompts, key=lambda item: item.slug):
        content = convert(item.content, item.variables)
        if not template_contract_is_valid(content, item.variables, "jinja2"):
            raise ValueError("转换后的 Jinja2 变量契约无效")
        for value in ("", "中文 <tag> {{原样}} 🙂\r\n末行\n"):
            values = {key: value for key in item.variables}
            old = item.content.format(**values) if item.variables else item.content
            if reference.from_string(content).render(**values).encode("utf-8") != old.encode("utf-8"):
                raise ValueError("转换改变了渲染原字节；需人工复核换行或字面语法")
        result.append(
            {
                "slug": item.slug,
                "content": content,
                "variables": copy.deepcopy(item.raw_variables),
                "format": item.format,
                "template_engine": "jinja2",
                "captured_version": item.version,
                "captured_content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return {
        "project_slug": bundle.project_slug,
        "source_revision": bundle.revision,
        "status": "proposal_only",
        "prompts": result,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="准备独立 Jinja2 完整基线提案")
    parser.add_argument(
        "--published-json", required=True, help="原始 published endpoint envelope，保留 variables 原结构"
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    bundle = _parse_bundle_envelope(json.loads(Path(args.published_json).read_text(encoding="utf-8")))
    proposal = prepare(bundle)
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        json.dump(proposal, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps({"status": "proposal_only", "prompts": len(proposal["prompts"]), "source_revision": bundle.revision})
    )


if __name__ == "__main__":
    main()
