"""启动期 catalog 完整性校验：声明即消费。

catalog 声明的每一项都必须有真实注入路径消费；否则管理员在 PromptHub 编辑该条目
后，bundle 校验通过、revision 变更、trajectory 记录新版本，但模型行为不变——
「发布成功但静默空转」比同步失败更危险，且会给出错误的因果归因。
"""

from __future__ import annotations

from app.core.prompt_catalog import assert_catalog_fully_consumed


def verify_prompt_catalog_consumers() -> None:
    """导入全部消费方模块后校验 catalog；缺失消费方直接 fail-fast。"""

    # 导入即注册：accessor 上的 register_prompt_consumer 装饰器在此生效。
    import app.ai.prompts.agent_loop  # noqa: F401
    import app.ai.prompts.prompt_manager  # noqa: F401

    assert_catalog_fully_consumed()
