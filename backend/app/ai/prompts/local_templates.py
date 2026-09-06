"""从随代码发布的本地文件加载完整 Prompt 集合。"""

from __future__ import annotations

from pathlib import Path

from app.core.prompt_catalog import PROMPT_SPECS
from app.core.prompt_template_engine import template_contract_is_valid

PROMPT_TEMPLATE_DIR = Path(__file__).with_name("prompt_templates")


def load_prompt_templates(template_dir: Path | None = None) -> dict[str, str]:
    """逐次读取完整目录；缺项、多项或变量不一致时立即拒绝。"""

    root = template_dir or PROMPT_TEMPLATE_DIR
    expected_slugs = {spec.slug for spec in PROMPT_SPECS}
    actual_slugs = {path.stem for path in root.glob("*.j2")}
    missing = sorted(expected_slugs - actual_slugs)
    extra = sorted(actual_slugs - expected_slugs)
    if missing:
        raise ValueError("本地 Prompt 文件缺少: " + ", ".join(missing))
    if extra:
        raise ValueError("本地 Prompt 文件多余: " + ", ".join(extra))

    templates: dict[str, str] = {}
    for spec in PROMPT_SPECS:
        path = root / f"{spec.slug}.j2"
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"本地 Prompt 文件无法读取: {spec.slug}") from exc
        if not content.strip():
            raise ValueError(f"本地 Prompt 正文不能为空: {spec.slug}")
        if not template_contract_is_valid(content, spec.variables, spec.template_engine):
            raise ValueError(f"本地 Prompt 变量契约不匹配: {spec.slug}")
        templates[spec.key] = content
    return templates


# 服务进程启动时读取一次。文件变更随正常发布和进程重启生效；运行期不轮询、
# 不监听，也不重新读取。
CODE_DEFAULT_PROMPT_TEMPLATES = load_prompt_templates()
