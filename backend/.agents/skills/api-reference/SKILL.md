---
name: api-reference
description: 从当前路由和 schema 核对 Fusion API 方法、认证、参数与 SSE 协议。
---

# API 合同定位

从[应用入口](../../../main.py)核对 router 的挂载前缀，再读[路由](../../../app/api)、[schema](../../../app/schemas)及依赖；不要把历史端点列表当作当前合同。

- 聊天、停止和续读看 [chat.py](../../../app/api/chat.py)；SSE 帧与结束语义看 [sse_encoder.py](../../../app/services/stream/sse_encoder.py)。
- 模型展示看 [models.py](../../../app/api/models.py)；管理员模型准入/可见性看 [admin_model_management.py](../../../app/api/admin_model_management.py)。当前业务模型使用 Proxy alias，不假定仍有旧凭据 CRUD。
- 文件接口看 [files.py](../../../app/api/files.py)，核对实际资源归属与失败状态。
- 验证认证应同时看路由依赖与应用中间件，不能因 handler 没写某个 Depends 就认定匿名开放。

查阅接口不需要启动服务；优先源码和现有测试。已有目标环境时可只读核对 OpenAPI。发送消息、上传、删除或管理员写入使用仓库的验收授权边界。返回示例只能代表已确认的 schema，完整 SSE 消费与终态还需相应行为证据。
