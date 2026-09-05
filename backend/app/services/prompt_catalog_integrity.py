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
    """apply 模式启动时校验 P0 过渡状态；不符即 fail-fast。

    - **已 attested**：`PRE_P0_CODE_ONLY_KEYS` 已改由 bundle 提供，必须校验 bundle 里
      这些项与代码默认值逐字节相等，以证实「已完成基线复核」这一声明属实；无有效 LKG
      时同样拒绝，因为此时无从证实。
    - **未 attested**：这些 key 被钉在代码默认值上（见 prompt_bundle 的过渡期钉住逻辑），
      与 P0 之前逐字节一致，因此可以直接在 apply 模式部署，无需经过 disabled 窗口。
      其余 key 仍从 bundle 取值，与 P0 之前一致；只有在连有效 LKG 都没有时，它们才会
      回落代码默认值而 P0 之前会回落 legacy，这一情形仍需拒绝。
    """

    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return

    from app.services.prompt_effective_map import (
        EffectiveBaselineMismatch,
        assert_p0_transition_gate,
        bundle_payload_contents,
    )

    payload = get_active_prompt_bundle_payload()
    if payload is None:
        raise EffectiveBaselineMismatch(
            "apply 模式下没有可校验的有效 LKG，无法证明模型可见正文与部署前一致。"
            "P0 之前 bundle 未命中会回落 legacy prompt_template，本次改动已删除该回落，"
            "此时启动会在未经基线校验的情况下改变正文。请修复 active bundle，或在确认"
            "无历史正文需要保留后置位 PROMPT_P0_BASELINE_ATTESTED。"
        )
    assert_p0_transition_gate(bundle_payload_contents(payload))
