---
name: add-provider
description: 为 Fusion 接入或排查新的模型提供商，核对 LiteLLM Proxy alias、目录能力与调用兼容性。
---

# 接入模型提供商

Fusion 使用 LiteLLM Proxy；[LLMManager](../../../app/ai/llm_manager.py)解析 alias 并返回 Proxy 参数，目录来自 [litellm_catalog.py](../../../app/ai/litellm_catalog.py)。实际凭据和上游路由由 Proxy 管理，不在 Fusion 恢复本地 provider 映射或旧凭据表。

先明确提供商、目标模型、协议及用户授权范围。沿当前目录、[模型管理服务](../../../app/services/model_management_service.py)和[reasoning 策略](../../../app/services/stream/reasoning_policy.py)判断：现有 Proxy 能力是否已覆盖，还是需要 Fusion 侧协议/能力适配。只需配置时说明准确缺口，不为“接入”硬加代码。

代码验证按受影响边界选取：

- [解析器测试](../../../test/test_llm_manager.py)：alias 与 Proxy 参数，目录可用/不可用及未知模型行为。
- [目录测试](../../../test/test_litellm_catalog.py)：元数据与能力映射。
- [模型管理测试](../../../test/test_model_management_service.py)：涉及准入或可见性时使用。
- 流式、reasoning、工具或多模态变化另选实际消费方的测试，覆盖成功和相关失败路径。

目标代码和测试通过只证明 Fusion 侧支持。Proxy 注册、远端凭据、真实模型调用和额度消耗须在用户明确授权范围内执行；已有授权可持续使用。凭据不得进入命令文本或报告，真实接入结论需目标 alias 的实际成功与相关降级证据。
