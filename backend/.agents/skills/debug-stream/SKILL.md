---
name: debug-stream
description: 定位 Fusion SSE 断线、停止、重连和内容恢复故障，关联后端生成与前端状态。
---

# 流式故障定位

先区分后台是否仍生成、Redis 是否已有终态、客户端是否接收、页面是否应用、刷新后是否恢复。只调查时读取已有脱敏日志与状态，不创建会话、不发 stop、不触发模型调用。

## 当前入口

| 路径 | 作用 |
|---|---|
| [chat_service.py](../../../app/services/chat_service.py) | 任务与上下文入口 |
| [stream 包](../../../app/services/stream/__init__.py) | 当前公开的生成/消费入口 |
| [runner.py](../../../app/services/stream/runner.py) | 后台生成编排 |
| [sse_encoder.py](../../../app/services/stream/sse_encoder.py) | Redis 到 SSE 的消费与结束 |
| [stream_state_service.py](../../../app/services/stream_state_service.py) | init、append、终态与取消 |
| [task_manager.py](../../../app/services/task_manager.py) | 进程内任务归属与取消 |
| [chat.py](../../../app/api/chat.py) | send、stop 与恢复端点 |

前端从根 `frontend/src/lib/chat/streamControllerRegistry.ts` 和实际 Redux/事件消费方追踪；核对会话、消息和当前请求身份，避免把切换会话误判成取消生成。

## 只读证据

- 按东八区时间窗口和 conversation/message/run 标识关联日志，保留失败原因。
- Redis key 形状先从当前 state service 与 Lua 核对。仅查询已知目标 key 的允许字段、lock 或 `XLEN`，不扫描全部会话，不输出 entry body。
- 对照主动停止、客户端断线、生成失败、预算结束各自的持久化和 UI 终态。服务事件顺序不能证明浏览器帧到达顺序。
- 缺少既有 Chrome 标签不阻止源码、目标测试和只读状态调查；明确剩余客户端证据，不能声称已真实复现。

获准真实验收后使用仓库 `fusion-acceptance` 和后端 `dev-test-api` 的边界。正常完成场景要实际消费到终态；主动断流只能用于对应故障场景，不能把固定短超时当正常验收成功。只操作本轮拥有的测试资源。
