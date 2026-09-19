---
name: api-overview
description: 定位 Fusion 后端架构与主要调用链；用于架构调查或跨模块改动。
---

# 后端架构入口

路径均相对 monorepo 的 `backend/`。先读[后端约定](../../../AGENTS.md)，按问题选择入口，不复制固定版本、worker 数或模型数量。

| 主题 | 当前入口 |
|---|---|
| 路由、生命周期与中间件 | [main.py](../../../main.py) |
| 聊天编排、上下文和任务创建 | [chat_service.py](../../../app/services/chat_service.py) |
| LLM 生成、工具轮次与 Redis 消费 | [stream 包](../../../app/services/stream/__init__.py) |
| 流状态、任务归属与终态 | [stream_state_service.py](../../../app/services/stream_state_service.py) |
| LiteLLM Proxy alias 解析与目录 | [llm_manager.py](../../../app/ai/llm_manager.py)、[litellm_catalog.py](../../../app/ai/litellm_catalog.py) |
| 工具与提示词 | [tools.py](../../../app/ai/tools.py)、[prompts](../../../app/ai/prompts) |
| 数据模型与认证 | [models.py](../../../app/db/models.py)、[security.py](../../../app/core/security.py) |

后台生成写 Redis，SSE 消费 Redis；客户端断线不应取消生成。Agent 工具调用、恢复和终态沿当前 runner 的实际调用链追踪，不能套用历史固定两轮模型。

架构细节按需查[核心数据流](../../../CHAT_CORE_DATA_FLOW.md)，部署看仓库根 `.github/workflows/` 与 `ops/`。历史文档和源码冲突时记录差异，以当前实现和运行证据判断。
