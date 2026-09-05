"""
提示词管理器
负责提供各种提示词模板，并支持模板变量替换
"""

from app.ai.prompts.templates import (
    FILE_ANALYSIS_PROMPT,
    FILE_CONTENT_ENHANCEMENT_PROMPT,
    GENERATE_SUGGESTED_QUESTIONS_PROMPT,
    GENERATE_TITLE_PROMPT,
)
from app.core.prompt_bundle import resolve_prompt_template, resolve_prompt_template_with_metadata
from app.core.prompt_catalog import register_prompt_consumer


class PromptManager:
    """提示词管理器"""

    def __init__(self):
        # 内置提示词模板映射
        self._templates = {
            "generate_title": GENERATE_TITLE_PROMPT,
            "generate_suggested_questions": GENERATE_SUGGESTED_QUESTIONS_PROMPT,
            "file_analysis": FILE_ANALYSIS_PROMPT,
            "file_content_enhancement": FILE_CONTENT_ENHANCEMENT_PROMPT,
        }

    def get_template(self, template_name: str) -> str:
        """获取指定名称的提示词模板"""
        if template_name not in self._templates:
            raise ValueError(f"未找到提示词模板: {template_name}")
        fallback = self._templates[template_name]
        return resolve_prompt_template(template_name, fallback)

    def format_prompt(self, template_name: str, **kwargs) -> str:
        """使用提供的参数格式化提示词模板"""
        template = self.get_template(template_name)
        try:
            return template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"格式化提示词模板时缺少参数: {e}")

    def format_prompt_with_metadata(self, template_name: str, **kwargs) -> tuple[str, dict]:
        """格式化 Prompt，并返回其 slug/version/revision 观测字段。"""

        if template_name not in self._templates:
            raise ValueError(f"未找到提示词模板: {template_name}")
        template, metadata = resolve_prompt_template_with_metadata(
            template_name,
            self._templates[template_name],
        )
        try:
            return template.format(**kwargs), metadata
        except KeyError as e:
            raise ValueError(f"格式化提示词模板时缺少参数: {e}")

    def add_template(self, name: str, template: str) -> None:
        """添加或更新提示词模板"""
        self._templates[name] = template


# 创建全局提示词管理器实例
prompt_manager = PromptManager()


@register_prompt_consumer("generate_title")
def get_generate_title_prompt() -> str:
    return prompt_manager.get_template("generate_title")


@register_prompt_consumer("generate_suggested_questions")
def get_generate_suggested_questions_prompt() -> str:
    return prompt_manager.get_template("generate_suggested_questions")


@register_prompt_consumer("file_analysis")
def get_file_analysis_prompt() -> str:
    return prompt_manager.get_template("file_analysis")


@register_prompt_consumer("file_content_enhancement")
def get_file_content_enhancement_prompt() -> str:
    return prompt_manager.get_template("file_content_enhancement")
