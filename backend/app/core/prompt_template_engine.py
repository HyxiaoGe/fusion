"""完整包只接受 sandboxed Jinja2；旧引擎仅保留在独立桥接构建中。"""

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

_JINJA = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)


def template_contract_is_valid(content: str, variables: tuple[str, ...], engine: str) -> bool:
    try:
        if engine == "jinja2":
            if not variables and any(marker in content for marker in ("{{", "{%", "{#")):
                return False
            fields = meta.find_undeclared_variables(_JINJA.parse(content))
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
        raise ValueError("不支持的 Prompt 模板引擎")
    except (TemplateError, KeyError, IndexError, AttributeError, TypeError) as exc:
        raise ValueError("Prompt 模板渲染失败或缺少必需参数") from exc
