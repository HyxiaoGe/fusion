# Issue #57：Agent 反馈与证据闭环实施计划

> 执行方式：使用 subagent-driven-development 分工实现，主代理集成并组织独立审查。

目标：修复部分工具失败覆盖正常回答的问题，统一搜索反馈与控制职责，展示实际 Observation，并用答案归属回归验证效果。

设计依据：用户在本对话批准的四阶段方案；#57 评审及 2026-09-08 独立复现。基线 `origin/master@21fefbcc`；工作分支 `codex/issue-57-agent-feedback`。当前已有 `f71fedb5` 文档记录保留，未跟踪的 `fusion-test.db` 不动。

## 全局约束

- 保留自实现 Agent-loop、本地英文模型提示词和现有服务分层；代码注释、文档、提交中文。
- 不新增自动改写答案的事实校验器，不引入框架、提示词服务或供应商专属修补规则。
- 有效工具证据与显式引用分别判断；不把有来源等同于每句话正确。
- 新旧轨迹兼容，新增字段可选；已有数据不迁移、不删除。
- 发布单独授权；本轮完成开发、测试、独立审查和可执行验收材料，不因等待发布而停止其他可完成工作。
- 用例先失败后实现；不改冻结 blind fixture 期望；仅 mock 外部 I/O。
- 子代理不提交、不推送、不发评论、不启动服务；主代理统一集成与提交。

## 任务 1：恢复判定与答案保留

文件：`backend/app/services/stream/agent_loop_state.py`、`agent_loop_driver.py`、`agent_loop_round_outcome.py`、`tool_recovery_evidence.py`、相关 stream 测试。

- [x] 固化不同 success/degraded/failed 顺序、无引用/有效引用/未知引用、全失败、领域工具后搜索恢复的回归。
- [x] 状态以累计结果和实际证据表达，不由同名工具最后一次结果决定任务成功；区分 degraded 与 failed。
- [x] 不因缺编号抛弃普通联网的有依据答案；保留无有效来源时诚实未完成与一次替代执行机会。
- [x] 普通联网、产品证据和 deep_research/knowledge 校验边界不相互侵入。
- [x] 跑目标测试并报告原始失败/修复后通过证据。

接口：保留 `record_tool_outcome` 与 `RecoveryEvidenceWorkset` 可用；新增信息不要求其余调用方猜测语义。与任务 3 共用 driver 时，仅任务 1 修改失败/defer 判定行，任务 3 添加上下文采集 hook。

## 任务 2：Observation、来源与搜索控制清理

文件：`backend/app/services/tool_handlers/web_search.py`、`url_read.py`、`schemas/chat.py`、`external/search_client.py`、`search_read_planner.py`、`source_candidate_ranker.py`、`stream/network_budget.py`、`search_budget.py`、配置默认值/schema、`ai/prompts/runtime_prompts.toml` 及对应测试。

- [x] 回归覆盖 query 不在标题中的回显、日期/站点缺失、原始 URL 不改写、跨批编号、重复来源。
- [x] 每工具消息清晰携带 query；可取得的发布日期和站点名显式传递，缺失保留未知；站点不冒充事件主体。
- [x] 搜索与阅读编号统一；安全边界/引用样板在单工具消息内只出现一次。
- [x] guidance 改为信息，不指令最多/最少读几篇，不凭西方白名单压制中文来源；保留来源去重和原始顺序。
- [x] 原始 URL 与去重 key 分离；保留 URL 安全措施。
- [x] 去掉会因重复搜索制造 degraded 的旧守卫与残留 repair 控制；保留总限额和来源去重。
- [x] 核实无消费者后删除死分支、提示词与配置项；旧 JSON 配置仍可解析，已删除项不再必填。
- [x] 提示词约束具体主体/数字/时间归属，区分事件与背景，允许未知；不得针对鱼类单题特判。
- [x] 跑 handler/planner/budget/schema 定点验证，列出需要主代理更新的跨模块消费者。

边界：本任务拥有 prompt TOML 和 SearchSource；任务 3 不编辑这些文件。`tool_round.py` 由任务 3 修改，任务 2 如需变更通过主代理集成。

## 任务 3：Observation 轨迹与上下文可见性

文件：`backend/app/services/stream/tool_round.py`、工具日志/轨迹服务及 schema、`agent_loop_driver.py`、上下文管理反馈；前端 trajectory 类型、API 和 `TrajectoryNodeDetailPanel` 等直接消费方。

- [x] 固化新记录展示实际 Observation、旧记录未采集、截断/凭据遮盖标记、重载读取的测试。
- [x] 在实际 tool message 构建完毕后有界采集文本，不从原始 data 重建；按 run/tool_call/llm_round 关联。
- [x] 保留原始载荷详情；普通业务内容可读，明确上限与遮盖/截断。
- [x] 每轮上下文裁剪记录移除的 tool_call_id 或等效可核对信息，区分生成与实际提交模型的可见范围。
- [x] 使用现有日志元数据/事件扩展，避免新建平行存储系统；采集失败不影响业务执行。
- [x] UI 明确展示工具返回与模型反馈、历史未采集；测试刷新通过 API 恢复。
- [x] 后端协议/持久化测试、前端 Vitest/tsc/build 合适层级验证。

## 任务 4：质量回归、集成与审查（主代理）

文件：新建 `backend/scripts/agent_feedback_eval.py`、固定证据 fixture 与专用测试、执行台账及报告。

- [x] 为鱼类原例及独立同主题不同主体案例建立有来源的固定证据；原例与构造例清楚区分。
- [x] 质量评估使用真实模型输出时，不预置答案；离线固定答案仅验证检测器，不能当模型质量通过。
- [x] 记录主体/数字/时间/引用归属、未知信息、工具恢复、证据缺口；不使用简单同段共现冒充语义验证。
- [ ] 候选版本真实模型回放待 dev 临时目录传输授权；公开 master 基线已完成 12 条真实输出及独立审阅，适配器限制已记录，不能用其证明候选效果。
- [x] 集成全部三块实现，目标检查通过后对跨模块协议扩大测试。
- [x] 独立审查完整 diff；主代理裁决、修复所有可达 P0/P1，再复核。
- [x] 更新 #57 对照清单：已修复、原评审需更正、另行跟踪，每项给依据；不擅自对外评论。
- [x] 提交可复核代码与成果报告；真实已部署页面验收与发布明确区分，不用旧页面证明新代码。

## 发布前的真实页面用例

1. 自然输入鱼类原问题：主体数据不能串用，未知数量测算依据不编造。
2. 自然投资分析：来源覆盖、阅读与分析质量，而非固定凑数量。
3. 领域工具不可用：可继续网络查证；全部来源不可用：诚实未完成。
4. 轨迹 Observation 与实际模型输入对应，刷新后保留；历史记录明确未采集。

此处需要新代码部署后执行，部署尚未授权，不计为已通过。

## 本轮验收状态

本地实现、独立审查与集成验证已完成，结果见 [修复报告](../reports/backend/2026-09-08-agent-feedback-issue57.md)。候选真实模型回放因自动审批拒绝未发布源码传输而等待明确授权；发布与自然登录页面验收未执行。
