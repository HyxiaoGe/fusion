"""启动期 catalog 完整性校验：声明即消费。

catalog 声明的每一项都必须有真实注入路径消费；否则管理员在 PromptHub 编辑该条目
后，bundle 校验通过、revision 变更、trajectory 记录新版本，但模型行为不变——
「发布成功但静默空转」比同步失败更危险，且会给出错误的因果归因。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.prompt_bundle import get_active_prompt_bundle_payload
from app.core.prompt_catalog import assert_catalog_fully_consumed


def verify_prompt_catalog_consumers() -> None:
    """导入全部消费方模块后校验 catalog；缺失消费方直接 fail-fast。"""

    # 导入即注册：accessor 上的 register_prompt_consumer 装饰器在此生效。
    import app.ai.prompts.agent_loop  # noqa: F401
    import app.ai.prompts.prompt_manager  # noqa: F401

    assert_catalog_fully_consumed()


def verify_p0_baseline_gate() -> None:
    """apply 模式启动时校验基线；未过门禁直接 fail-fast。

    首次部署 P0 时库里可能已存在一个从未经过门禁的 active bundle，因此激活时的
    门禁不足以覆盖——启动时必须再查一次。

    「无有效 LKG」是正常可达状态（首次部署、bundle 损坏、catalog 扩容后旧 payload
    整体失效），不是可以跳过门禁的理由：

    - P0 之前，11 项里那 6 项在 bundle 未命中时会回落 legacy ``prompt_template``；
    - 本次改动删除了该回落，同样情形下直接落到代码默认值。

    因此在过渡未 attested 且无有效 LKG 时启动，服务会在没有做过 capture 与逐 key
    复核的情况下改变模型可见正文。这一分支必须 fail closed。
    """

    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return

    from app.services.prompt_effective_map import (
        EffectiveBaselineMismatch,
        assert_p0_transition_gate,
        bundle_payload_contents,
    )

    if settings.PROMPT_P0_BASELINE_ATTESTED:
        return

    payload = get_active_prompt_bundle_payload()
    if payload is None:
        raise EffectiveBaselineMismatch(
            "apply 模式下没有可校验的有效 LKG，无法证明模型可见正文与部署前一致。"
            "P0 之前 bundle 未命中会回落 legacy prompt_template，本次改动已删除该回落，"
            "此时启动会在未经基线校验的情况下改变正文。请先完成 effective baseline "
            "capture 与逐 key 复核并置位 PROMPT_P0_BASELINE_ATTESTED，或修复 active bundle。"
        )
    assert_p0_transition_gate(bundle_payload_contents(payload))
