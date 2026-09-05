"""启动期 catalog 完整性校验：声明即消费。

catalog 声明的每一项都必须有真实注入路径消费；否则管理员在 PromptHub 编辑该条目
后，bundle 校验通过、revision 变更、trajectory 记录新版本，但模型行为不变——
「发布成功但静默空转」比同步失败更危险，且会给出错误的因果归因。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.core.prompt_bundle import get_active_prompt_bundle_payload
from app.core.prompt_catalog import assert_catalog_fully_consumed


def verify_prompt_catalog_consumers() -> None:
    """导入全部消费方模块后校验 catalog；缺失消费方直接 fail-fast。"""

    # 导入即注册：accessor 上的 register_prompt_consumer 装饰器在此生效。
    import app.ai.prompts.agent_loop  # noqa: F401
    import app.ai.prompts.prompt_manager  # noqa: F401

    unconsumed = assert_catalog_fully_consumed()
    if unconsumed:
        logger.warning(
            "prompt catalog: 以下条目没有生产消费方，热更新对其不生效，需在 P1 收敛: %s",
            ", ".join(unconsumed),
        )


def verify_p0_baseline_gate() -> None:
    """apply 模式启动时校验当前 active bundle；未过门禁直接 fail-fast。

    首次部署 P0 时库里可能已存在一个从未经过门禁的 active bundle，因此激活时的
    门禁不足以覆盖——启动时必须再查一次当前生效的 bundle。
    """

    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return

    from app.services.prompt_effective_map import assert_p0_transition_gate, bundle_payload_contents

    payload = get_active_prompt_bundle_payload()
    if payload is None:
        return
    assert_p0_transition_gate(bundle_payload_contents(payload))
