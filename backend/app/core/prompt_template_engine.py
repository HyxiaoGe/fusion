"""完整包引擎契约；桥接期明确区分旧格式与 sandboxed Jinja2。"""

import string

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

from app.core.prompt_catalog import CATALOG_VERSION_BY_ENGINE

_JINJA = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)


def payload_template_engine(payload: dict) -> str:
    """同一包只允许一个引擎，catalog 与引擎严格绑定。"""
    engines = {item.get("template_engine") for item in payload["prompts"].values()}
    if len(engines) != 1:
        raise ValueError("完整 Prompt bundle 不允许混合引擎")
    engine = engines.pop()
    if engine not in CATALOG_VERSION_BY_ENGINE or payload.get("catalog_version") != CATALOG_VERSION_BY_ENGINE[engine]:
        raise ValueError("Prompt catalog 与引擎不匹配")
    return engine


def template_contract_is_valid(content: str, variables: tuple[str, ...], engine: str) -> bool:
    try:
        if engine == "jinja2":
            if not variables and any(marker in content for marker in ("{{", "{%", "{#")):
                return False
            fields = meta.find_undeclared_variables(_JINJA.parse(content))
        elif engine == "none":
            if not variables:
                return True
            fields = {name for _, name, _, _ in string.Formatter().parse(content) if name is not None}
        else:
            return False
        if fields != set(variables):
            return False
        render_prompt_template(content, engine, {name: "x" for name in variables})
        return True
    except (TemplateError, ValueError, TypeError, AttributeError, IndexError, KeyError):
        return False


def render_prompt_template(content: str, engine: str, values: dict) -> str:
    """只按冻结元数据渲染，不从正文推断引擎，不把变量再次当作模板。"""
    try:
        if engine == "jinja2":
            return _JINJA.from_string(content).render(**values)
        if engine == "none":
            return content.format(**values)
        raise ValueError("不支持的 Prompt 模板引擎")
    except (TemplateError, KeyError, IndexError, AttributeError, TypeError) as exc:
        raise ValueError("Prompt 模板渲染失败或缺少必需参数") from exc
