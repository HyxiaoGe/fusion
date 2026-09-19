# 后端约定

遵循[根协作约定](../AGENTS.md)，本文件补充 `backend/` 的工程边界。

- 分层依赖保持 `API → Service → AI → Data`。鉴权与资源归属沿当前依赖注入链核对，异步入口不引入阻塞 I/O。具体约定按需查[核心数据流](CHAT_CORE_DATA_FLOW.md)和[编码约定](docs/CODING_CONVENTIONS.md)。
- 模型调用通过 LiteLLM Proxy alias；以[解析器](app/ai/llm_manager.py)和[目录](app/ai/litellm_catalog.py)为准，不重新引入本地 provider/凭据路由表。
- 后台生成独立于 HTTP/SSE 连接。断线、主动停止、生成失败和预算耗尽是不同路径；修改时检查任务归属、终态持久化、Redis 状态与刷新恢复，避免旧请求终结新任务。
- 工具失败应以真实结果回到模型决策上下文；来源必须有可用内容才能支撑结论。日志、服务决策和模型输出分别用 `run_id`、`llm_round_id`、`tool_call_id` 关联，不把服务兜底表述成模型成功完成。
- Agent、流式和持久化改动，从 [stream 包入口](app/services/stream/__init__.py)、实际调用方和相应测试确认范围，不以历史文件名猜实现。

## 验证入口

从 `backend/` 使用项目已有 Python 环境运行目标 `python -m pytest test/受影响测试文件.py -q`、`python -m ruff check 受影响的文件` 和必要的 `python -m ruff format --check 受影响的文件`。

选择能复现故障的输入、依赖失败或状态转换，修复前后对照同一行为。共享协议或生命周期改动检查相关生产者、消费方与历史数据兼容。完整 CI 收集入口见 [Linux 检查脚本](.github/scripts/linux-build-and-test.sh)，新增回归应能被实际入口收集。

只调查时不创建真实会话、不消耗模型额度；已授权的真实验收遵循[验收 skill](../.agents/skills/fusion-acceptance/SKILL.md)。审查只报告当前变更引入且具有可达严重后果的 P0/P1，证据不足和 P2/P3 建议不阻塞合并。
