"""
提示词管理器
负责提供各种提示词模板，并支持模板变量替换
"""

from app.ai.prompts.local_templates import CODE_DEFAULT_PROMPT_TEMPLATES
from app.ai.prompts.templates import (
    FILE_ANALYSIS_PROMPT,
    FILE_CONTENT_ENHANCEMENT_PROMPT,
    GENERATE_SUGGESTED_QUESTIONS_PROMPT,
    GENERATE_TITLE_PROMPT,
)
from app.core.prompt_bundle import freeze_prompt_bundle, resolve_prompt_template_with_metadata
from app.core.prompt_catalog import register_prompt_consumer
from app.core.prompt_snapshot import use_prompt_snapshot
from app.core.prompt_template_engine import render_prompt_template


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

    def resolve_template_with_metadata(self, template_name: str) -> tuple[str, dict]:
        """唯一解析点：get_template 与 format_prompt* 全部经由此处。

        注册到 catalog 的消费方 accessor 也绑定在这里，因此「注册的函数」与
        「生产真正调用的函数」是同一个，绕过它就等于绕过注册。
        """

        if template_name not in self._templates:
            raise ValueError(f"未找到提示词模板: {template_name}")
        return resolve_prompt_template_with_metadata(template_name, self._templates[template_name])

    def get_template(self, template_name: str) -> str:
        """获取指定名称的提示词模板"""
        template, _metadata = self.resolve_template_with_metadata(template_name)
        return template

    def format_prompt(self, template_name: str, **kwargs) -> str:
        """使用提供的参数格式化提示词模板"""
        template, metadata = self.resolve_template_with_metadata(template_name)
        return render_prompt_template(template, metadata["template_engine"], kwargs)

    def format_prompt_with_metadata(self, template_name: str, **kwargs) -> tuple[str, dict]:
        """格式化 Prompt，并返回其 slug/version/revision 观测字段。"""

        # 标题、推荐问题与文件分析是独立模型调用，即使异步任务继承了主 Run
        # 的 ContextVar，也必须重新冻结自己的来源。附件包装仍走 format_prompt。
        with use_prompt_snapshot(freeze_prompt_bundle(CODE_DEFAULT_PROMPT_TEMPLATES)):
            template, metadata = self.resolve_template_with_metadata(template_name)
        return render_prompt_template(template, metadata["template_engine"], kwargs), metadata

    def add_template(self, name: str, template: str) -> None:
        """添加或更新提示词模板"""
        self._templates[name] = template


# 创建全局提示词管理器实例
prompt_manager = PromptManager()


# 注册绑定到真实生产解析路径：四处生产调用（chat_service 生成标题、
# suggested_question_service 生成推荐问题、file_processor 文件分析、
# message_builder.inject_file_content 文件内容包装）都经由
# resolve_template_with_metadata，注册的就是该解析点，
# 而不是另建一层只在注册处出现的包装。
def _register_consumed(key: str) -> None:
    def accessor() -> str:
        template, _metadata = prompt_manager.resolve_template_with_metadata(key)
        return template

    accessor.__name__ = f"resolve_{key}"
    register_prompt_consumer(key)(accessor)


for _consumed_key in (
    "generate_title",
    "generate_suggested_questions",
    "file_analysis",
    "file_content_enhancement",
):
    _register_consumed(_consumed_key)
del _consumed_key
