# 停止轨迹修复验证记录

## 根因与改动

PR #49 发布后，真实天气结果出现后停止并刷新，结果卡片保留，但最后 LLM 取消和 Run 中断事件被 Redis 所有权门禁挡住，未进入 trajectory。原始对话、Run 和事件序号见 [首次发布验收](2026-09-07-agent-loop-dev-release.md)。

- CompositeWriter 仅在 StreamOwnershipLostError 时把明确取消 terminal 送入该 Run 的原轨迹 recorder，跳过 progress，继续重抛原异常。Redis 不解冻，其他消息、工具结果、计划和非所有权故障保持原行为。
- LLM 取消时即使实时流已冻结，也完成一次性原候选详情调度；普通轮次/总结轮次把所有权失效归入取消，保留调用方原异常。
- 无数据库或共享事件协议变化，无前端代码变化，无历史记录回填。

## 本地验证

- 红测复现：真实 emitter + LLM lifecycle + queued recorder + SQLite 新 Session 查询，已输出/无正文两种取消原先丢 terminal；普通/总结所有权竞态与 superseded 账本矩阵也失败。随后完成最小修复。
- 核心目标 12 文件：`315 passed + 82 subtests`；归因、详情、查询和安全 payload 4 文件：`60 passed + 91 subtests`。合计 `375 passed + 173 subtests`。
- 使用既有 `/Users/sean/code/fusion/fusion-api/.venv/bin/python`，从 backend 目录以 `TZ=Asia/Shanghai LITELLM_LOCAL_MODEL_COST_MAP=True` 运行。早期临时 Python 环境缺少 auth_service_client 导致停止接口测试收集失败，切回完整环境后通过；未修改依赖或鉴权实现。
- 九个变动 Python 文件 Ruff check / format check 通过，架构检查通过（原有四项测试覆盖提示保留），diff check 通过。
- 独立审查无可达 P0/P1；业务与测试差异 SHA-256 `ca2b2380f7773c664082d78a051eeca845a4626dc376b2d5e2f0e39200e6f801`，四个业务文件差异 SHA-256 `eeaa4fef36168549faaef0949b1db0ff1d482b7740e494cd84f353600740320f`。

## 发布与真实复验

本记录的当前阶段为本地验证通过、准备进入已获授权的 dev 发布。PR/CI/运行镜像和真实停止复验结果在完成后追加；不能把本地回归视为已上线修复。

## 保留范围

工具执行中取消后产生的普通 failed 结果或计划快照仍可能因 Redis 拒绝留下轨迹缺口，继续如实降级。此次修复保证明确取消事件的记录，不声称全部 stop 场景轨迹完整；多城市路由与产品回答改写策略未修改。
