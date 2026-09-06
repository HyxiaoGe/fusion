"""在服务边界冻结代码默认值与分类器正文，不在分类预算内解析来源。"""

from functools import wraps

from app.ai.prompts.local_templates import CODE_DEFAULT_PROMPT_TEMPLATES
from app.core.prompt_bundle import freeze_prompt_bundle
from app.core.prompt_snapshot import PromptBundleSnapshot, use_prompt_snapshot


def freeze_runtime_prompt_bundle() -> PromptBundleSnapshot:
    from app.services.stream.run_capability_model_classifier import _system_prompt

    return freeze_prompt_bundle(CODE_DEFAULT_PROMPT_TEMPLATES, classifier_prompt=_system_prompt())


def with_call_config_prompt_snapshot(prepare_fn):
    """异步准备过程中的文件包装和消息组装统一使用 call config 的冻结来源。"""

    @wraps(prepare_fn)
    async def prepare_with_snapshot(*args, **kwargs):
        snapshot = kwargs["call_config"].prompt_bundle_snapshot
        if snapshot is None:
            raise ValueError("Run 缺少分类前冻结的 PromptBundleSnapshot")
        with use_prompt_snapshot(snapshot):
            return await prepare_fn(*args, **kwargs)

    return prepare_with_snapshot
