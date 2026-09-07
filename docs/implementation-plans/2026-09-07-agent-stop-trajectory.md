# 停止后的轨迹终态补齐

## 现象与边界

在 PR #49 已部署的 `11906526` 上，真实 Chrome 天气任务收到卡片后点击停止，刷新仍保留卡片，但轨迹 `cadadcd66a674a31aee127ca46d1e78d` 只有 sequence 0–20，期望到 22；缺少 LLM 取消与 Run 中断事件，最终标为 `finalize_mismatch`。

停止接口先冻结 Redis 所有权，随后取消后台任务。当前复合 writer 要求 Redis 成功后才写轨迹，因此后台真实产生的取消终态无法进入独立账本。

本次只补齐停止后的观察记录，不重新开放 Redis、不恢复模型或工具执行、不修改客户端 partial 权限、不修改多城市能力路由和产品回答策略，也不修补历史事件。沿用本任务已获授权的 dev 发布流程。

## 实施与验证

1. 用真实 emitter、LLM lifecycle、排队 recorder 和 SQLite Session 复现先冻结 Redis 再取消；要求刷新查询可取得终态、原输出归因和连续序号。
2. 仅为明确取消终态提供账本接纳路径，之后重抛同一所有权异常；LLM 详情调度仅执行一次。普通轮次和收尾轮次将所有权失效归为取消。其他事件和 Redis 故障继续保留原失败边界，验证不向已冻结流或 progress sink 写入。
3. 运行相关生命周期、轨迹、工具与权限回归，独立审查当前差异，再走 PR CI、合并和 dev 发布。
4. 复用原 Chrome 标签创建自然天气任务，结果出现后停止，检查卡片、取消归因、轨迹完整性、刷新与 network/console。

## 保留的真实降级边界

只接纳 `run_interrupted`、`llm_round_cancelled`、`retrieval_cancelled` 和取消状态的 `tool_attempt_completed`。工具中途取消后产生的普通失败结果、计划快照仍须经过原 Redis 写入门禁；缺失这些事件时继续显示降级，不扩大白名单或回退序号掩盖缺口。
