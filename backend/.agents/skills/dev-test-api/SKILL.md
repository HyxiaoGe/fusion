---
name: dev-test-api
description: 在明确授权后调用既有 dev API 做真实验收；可能创建会话、写 dev 状态并消耗模型额度。
argument-hint: [测试场景描述]
allowed-tools: Bash
---

# Dev 服务器 API 真实验收

本 skill 不是默认单元测试步骤。调用聊天、模型、凭据或文件端点可能创建数据、发送消息、消耗额度或改变 dev 状态，必须先取得用户对本次真实 dev 验收的明确授权。

## 安全前置

- 不在仓库、skill、命令历史或报告中保存固定账号、密码、client id、API key、cookie 或 token。
- 凭据只从用户已授权的安全来源取得，并以 `FUSION_DEV_ACCESS_TOKEN` 等环境变量注入；执行前关闭 shell trace，命令与输出均不得回显凭据。
- 只使用当前场景需要的最小权限与最短有效期；验收记录只保留脱敏状态码、资源 id、终态和清理结果。
- 未获得真实验收授权时，只运行本地自动化测试或读取已有的脱敏日志/状态，不调用写接口。

## 已授权后的最小流程

1. 写下测试资源前缀、预计写入、模型消耗、停止条件和清理方式。
2. 先调用不写状态的健康与能力端点，确认目标环境和模型 id；不要猜固定模型。
3. 用安全注入的 `FUSION_DEV_ACCESS_TOKEN` 调用目标端点。流式请求必须设置超时并保存脱敏的 conversation/message id。
4. 若场景包含 stop、重连或历史恢复，只操作本轮创建的资源；不得复用未知归属的会话。
5. 验证成功、失败/降级与终态；按授权范围清理本轮测试数据，并记录无法清理的残留。

## 请求模板

以下模板只有在上述授权成立后使用；占位 payload 必须由当前路由和场景确定：

```bash
ssh dev "curl --fail-with-body --silent --show-error \
  -H 'Authorization: Bearer ${FUSION_DEV_ACCESS_TOKEN}' \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8002/api/{authorized-endpoint} \
  --data '{authorized-json-payload}'"
```

不得直接修改 dev 服务器代码或配置；发布状态核验使用 `dev-verify`。
