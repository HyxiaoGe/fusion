---
name: add-provider
description: Add a new LLM provider to fusion-api. Use when integrating a new AI model service.
argument-hint: <provider-name>
---

# 添加新 LLM 提供商

Fusion API 通过 LiteLLM 统一接口，添加新提供商只需配置映射，不需要写 adapter。

## 步骤

### 1. 添加 LiteLLM 前缀映射

编辑 `app/ai/llm_manager.py`：

```python
PROVIDER_LITELLM_PREFIX = {
    # ...现有映射...
    "{provider}": "{litellm_prefix}",  # 新增
}
```

LiteLLM 前缀说明：
- **OpenRouter 路由**：用 `openrouter/{provider}`（如 `openrouter/openai`、`openrouter/anthropic`）
- **直连**：用对应前缀（如 `deepseek`）
- **OpenAI 兼容接口**：统一用 `openai`，通过 `api_base` 指定实际地址

### 2. 如需自定义 api_base

如果新提供商使用 OpenAI 兼容接口但有自己的 base URL，加入：

```python
CUSTOM_BASE_URL_PROVIDERS = {"qwen", "volcengine", "wenxin", "hunyuan", "{provider}"}
```

凭证的 `base_url` 字段会被自动读取并传给 LiteLLM。

### 3. 如支持 reasoning/thinking

如果模型支持推理/思考过程输出，加入 `StreamHandler`：

```python
class StreamHandler:
    REASONING_PROVIDERS = {"deepseek", "qwen", "xai", "volcengine", "{provider}"}
```

### 4. 添加环境变量

在 `.env.example` 和 `docker-compose.yml` 中添加：

```bash
{PROVIDER_UPPER}_API_KEY=
{PROVIDER_UPPER}_API_BASE=  # 如需自定义 base URL
```

### 5. 在数据库中注册模型

通过 `POST /api/models/` 端点或直接在 `model_sources` 表中插入模型定义：

```json
{
  "model_id": "{provider}-model-name",
  "name": "模型显示名称",
  "provider": "{provider}",
  "capabilities": {"deepThinking": true},
  "enabled": true
}
```

然后通过 `POST /api/models/{model_id}/credentials` 添加凭证。

## 当前支持的提供商

| 提供商 | 前缀 | 路由方式 | reasoning |
|--------|------|---------|-----------|
| openai | openrouter/openai | OpenRouter | - |
| anthropic | openrouter/anthropic | OpenRouter | ✓ |
| google | openrouter/google | OpenRouter | ✓ |
| xai | openrouter/x-ai | OpenRouter | ✓ |
| deepseek | deepseek | 直连 | ✓ |
| qwen | openai | OpenAI 兼容（自定义 base） | ✓ |
| volcengine | openai | OpenAI 兼容（自定义 base） | ✓ |

OpenRouter 路由的 provider 只需在凭证中存 OpenRouter API Key，无需 base_url。

## 验证

1. 先为 provider 映射、凭据参数组装、reasoning 能力与失败降级补 RED 测试，再运行对应 pytest 和 Ruff。
2. 不在 skill 或命令历史中保存固定账号、密码、client id、API key 或 token。真实凭据只能来自用户已授权的安全来源，以环境变量或受控 secret 注入；命令不得回显其值。
3. 创建模型/凭据、创建会话、发送消息或触发真实模型调用会写 dev 状态并消耗模型额度，只能在用户明确授权真实 dev 验收后执行。
4. 获得授权后，复用已有 dev 服务与安全注入的短期访问令牌，验证一次成功流、一次无效凭据失败和一次能力降级；记录脱敏状态码、模型 id 与终态，不记录请求凭据或完整响应内容。
