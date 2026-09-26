# Fusion 执行台账

> 本文件是 Fusion monorepo 的执行记录入口。评估路线或完成状态时，按主题查相关记录、当前代码与 Git 历史；实施设计按需查 `docs/implementation-plans`、`docs/specs`，模型验收查 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`。历史记录不能替代当前 CI、运行版本或产品证据。

## 使用规则

- 不把 Codex memory 当执行记录；memory 只能作为偏好和约束提示。
- 每次重大功能、核心链路、发布门禁或真实回归完成后，在本文件补一条记录。
- 如果方向已经在“已完成基线”或“不要重复建议”中出现，不得作为下一步建议重新提出，除非用户明确要求返工或扩展。
- 如果当前文件和当前代码、Git、CI 或运行证据冲突，以当前证据为准；调查时指出差异，获准编辑时补充更新，不改写历史事实。

## 已完成基线

### 2026-09-23 #115 无响应停止验收与 #71 后续切换判据

#115 已合并，dev UI 发布台账与运行镜像均为 `1ba16d7`。复用原 Chrome 登录态、定向拦截恢复停止请求使其无响应后，页面有界显示“停止结果未确认”，下一轮正常完成且刷新保留；只读数据库证实原 run 最终 interrupted、下一轮 completed。停止请求本身未送达，不能归因为该请求即时取消；原轮未确认提示刷新后不持久。合并后的 master CI `35809108311` 在管理页详情异步断言失败，测试修复已提交 PR #116，本地完整前端 2598 项、PR required gate 通过，尚未合并。动态发现仍未切默认；Skill、深度研究、续跑及业务证据是分阶段切换条件。详见[核实与切换记录](reports/2026-09-23-issue71-next-phase.md)。

### 2026-09-22 停止确认二次修复（本地完成）

针对 #113 发布后的两条真实失败路径，修复恢复确认后的原run即时终态、普通停止超时后按原run核实与未确认提示，并封住恢复流缺task时的会话级取消。相关359项及追加后的发送hook87项、触及文件lint与构建通过，独立审查未见新增可达P0/P1；尚不代表新版本真实页面验收通过。详见[本轮报告](reports/2026-09-22-stop-confirmation.md)。

### 2026-09-22 停止状态与搜索故障修复发布（页面验收仍有缺口）

PR [#113](https://github.com/HyxiaoGe/fusion/pull/113) 已合并为 `755d481d`；PR/主干 CI 与 dev 发布全部成功，22:23:54（Asia/Shanghai）确认 API/UI 镜像和台账匹配。没有切换默认动态发现。

发布后真实 Chrome 验收：动态发现轨迹文案可见；停止后下一轮问候正常完成。但恢复流停止返回 cancelled=true、DB interrupted 后，页面仍运行中，直到详情刷新才纠正；普通发送停止请求489ms被中止，DB已interrupted但页面同样需刷新。不能写“停止体验验收通过”。早停无任务身份时只停止本地接收的限制也实测出现。两条文档检索只执行 tool_search、没有真正搜索/读取，不计联网成功或供应商故障复现。所有验收run已终态。详细本地证据：`/Users/sean/code/fusion/release-stop-search-20260922/REPORT.md`。

### 2026-09-20 产品观测成组发布与身份路由取证

#105 已合入；#104/#106 经面向 master 的自动检查，通过单次 merge commit `4678e3f0` 成组合入并部署 dev，避免无产品结果早退的观测空窗。API、adapter、worker 镜像与发布台账及健康检查通过，UI skipped。观测表迁移为 `b2c7d9e4f610`；14:38（Asia/Shanghai）实测空表，首条时间未产生，不能把部署时间当新观测期起点，详见 [#102 记录](https://github.com/HyxiaoGe/fusion/issues/102#issuecomment-5748178392)。

#99 当前环境实测启用 3 个 MCP server、可构建 3 个通用授权 handler，已按 not_planned 关闭并保留机制边界；历史 0/201 仅为 mcp_explicit 路由次数，不证明无人尝试。#100 历史身份原句实测与有限收窄说明见 [身份路由报告](../backend/docs/IDENTITY_ROUTING_NARROWING.md)；6 条复合请求收窄前即失败，本次未改变，不可写成历史原句全部通过。

### 2026-09-19 开发协作规则更新（本地验证）

AGENTS/CLAUDE 收敛到当前单仓约定，开发与发布技能迁入仓库，新增真实验收技能并更新后端 8 个技术入口。扩展的 19 项协作检查、12 个 skill 格式检查及四个独立只读场景检查完成；不代表实际开发质量或速度提升已经验证。全局规则已备份并链接本次工作树，未推送、合并或部署。详见[更新与验证报告](reports/2026-09-19-development-guidance.md)。

| 领域 | 状态 | 关键证据 |
|---|---|---|
| Agent loop 基础拆分 | 已完成一轮 | `fusion-api` 2026-06-27 至 2026-06-28 相关 plan/spec；runner 状态、runtime、driver、summary 等拆分记录 |
| Agent 进度协议和前端状态 | 已完成一轮 | `fusion-api` / `fusion-ui` 2026-06-28 之后的 agent progress、执行过程、直接回答计划回归 |
| Search / Read Planner | 已完成 v1.1/v1.2/v1.3 一轮 | `fusion-api` commits `3933496`, `9bc7a9e`, `c4177ad`, `65bd446` |
| SourceCandidateRanker / Evidence Ledger | 已完成一轮 | `fusion-api` commits `78d3027`, `19423c3`；`fusion-ui` evidence 相关展示提交 |
| 工具过程 / 回答依据 UI | 已完成多轮收敛 | `fusion-ui` commits `1bc7fc5`, `0ea9aaa`, `fc8ede7`, `2391bee`, `cf65397` |
| 多模型验收矩阵 | 已完成并固化 | `fusion-api` commits `1068bf7`, `ab1d0c9`, `1c5364e`, `70f80e6`, `3b0b627`；`backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md` |
| 模型能力契约和展示 | 已完成一轮 | `fusion-api` commits `ed1da51`, `4ef734e`, `13c30ba`, `1e64334`；`fusion-ui` commits `f3a5033`, `272517e`, `9f29482`, `a33559a`, `bd1dbb5` |
| 小米 MiMo v2.5 模型更新 | 已完成 | `fusion-api` 模型目录治理和同步相关 commits `809b24b`, `4d29ae5`, `98ba0b9`, `855a39b` |
| CI / 发布门禁 | 已完成一轮 | `fusion-api` commit `9923fd0`；`fusion-ui` commits `bf9a112` 至 `68b7c9e`，以及 `014bb67` / `24601de` 指标修正 |
| Runtime Config 落库治理 | 已完成一轮 | `fusion-api` commits `092deb8`, `56ba600`, `d2af24f`, `6a69e55` |
| Runtime Config UI 观察面板 | 已完成一轮 | `fusion-ui` commits `fcff362`, `a27d3df`, `ea94879` |
| LiteLLM 观测标签透传 | 已完成修正 | `fusion-api` commits `aebf7a4`, `ab8eacc` |
| 图片文件解析链路修复 | 已完成 | `fusion-api` commit `21c2cf5` |
| PromptHub 正式迁移 | 已完成 | PromptHub published bundle / project-bound service token；Fusion 完整 LKG、`disabled -> shadow -> apply`、版本观测、管理保留域和部署持久化 smoke |
| Redis Stream 故障、展示与断线续传可靠性 | 已完成 P1 | `fusion-api` commit `363233f` / `fusion-ui` commit `e7d596d`；原子初始化/追加/检查/终态、task/message fencing、fail-fast、Redis 就绪检查、自适应打字机、安全 SSE 游标、有限自动重连与部分回答保留 |
| 流式续传统一与页面恢复 | 已完成 P2 收尾 | `fusion-api` commits `70f391d`, `6d05512`, `18fb967` / `fusion-ui` commits `d4f66a9`, `765974c`；普通发送、Agent continuation 与页面恢复统一有限重连、安全游标、原子停止、部分输出持久化和取消终态收敛 |
| 生产性能基线 | 已完成首轮 | `backend/docs/performance/2026-07-11-production-baseline.md`；API 单核约 145 RPS，公网 HTTP/2 P95 2.38 s，真实 SSE 1→3→5 并发 9/9 成功并完成零残留清理 |
| 生产完整性能矩阵 | 已完成 L1-L4 | `backend/docs/performance/2026-07-12-production-full-matrix.md`；L1 600/600、L2 28/28、L3 恢复 9/9 + 停止/持久化 9/9、L4 30 分钟 60/60；0 重启/OOM，测试数据零残留，管理员页面导入并刷新持久化 |
| 长对话上下文预算管理 | 已完成第一阶段 | `fusion-api` commits `f5152be`, `b721edd`, `fc6ad22`；已知模型窗口按 85% 触发、75% 目标裁剪最旧完整 turn/工具事务，未知窗口保持兼容，估算失败 fail-closed；15 秒 SSE heartbeat；`backend/docs/performance/2026-07-13-context-management-baseline.md`；生产 60%/80%/90% 阶梯与登录态 Chrome 刷新恢复通过 |
| 对话上下文状态展示 | 已完成 v1 | `fusion-api` / `fusion-ui` 本次提交；以最后一次 LLM round 为口径推送 `context_status_updated` v2，并在 assistant `usage.context` 中持久化可刷新快照；前端展示本轮实际/预计 Token、上下文窗口、剩余比例和自动裁剪明细，未知窗口、旧历史、失败状态与重连均安全降级 |
| 管理员审计中心 | 已完成 v1 | `fusion-api` commit `1ec0f01`、`fusion-ui` commits `f2517ca` / `5e47692`；全局用户/对话、消息、Agent/tool、文件元数据、压测留存和访问审计；`backend/docs/acceptance/2026-07-11-admin-audit-center-v1.md` |
| 管理员压测审计详情 | 已完成 v1.1 | `fusion-api` commit `64d3452`、`fusion-ui` commit `2e3d857`；列表与详情安全契约分离、存量脏数据安全降级、L1-L4/资源/清理结果结构化详情、按需请求、响应式布局和管理页 no-store；生产登录态 Chrome 验收通过 |
| 管理员审计安全与身份展示 | 已完成 v1.2 | `fusion-api` commit `d036d89`、`fusion-ui` commit `9d1075d`；审计内容严格白名单投影、签名 URL/令牌脱敏、历史 schema 安全降级、用户昵称/用户名/ID 三元组、详情竞态隔离、Agent/tool 独立分页、压测指标语义与管理页 CSP 收紧；对抗式复审及生产登录态 Chrome 验收通过 |
| 管理员用户详情与对话联动 | 已完成 v1.3 | `fusion-ui` commit `d2d473b`；用户详情改为当前视口立即可见的弹窗，统一 loading/错误/重试并隔离迟到请求；可从详情直接进入该用户对话，用户筛选仅作用于本次关联导航；生产登录态 Chrome 验收通过 |
| Run 级 Skills 运行时 | 已完成 MVP | `fusion-api` merge commits `64049e5`（PR #76 引入 Run 级 Skills 运行时）、`3ba19f9`（PR #77 修正 skills CRLF 与 release hash）、`22f8625`（PR #79 修正 fresh release intent 路由）；Skill registry (`app/ai/skills/registry.py`)、`verified-research/1.0.0` skill、Run capability 路由、Trajectory API 与 skills 详情端点、agent loop wiring 与 previous run skill release，含配套测试 |
| 管理员时间展示与返回路由 | 已完成 v1.4 | `fusion-ui` commit `402644d`；对话列表展示创建/更新时间；Tab、用户详情、用户对话筛选、对话详情与压测详情以 URL 为事实源，支持手势返回、刷新恢复、深链接、旧压测记录跨页加载与 403 净 URL；生产登录态 Chrome 验收通过 |
| 管理员模型运营中心 | 已完成 v1 | `fusion-api` commits `5624241`, `1a2b456`、`fusion-ui` commit `3097c6b`；LiteLLM 当前目录与历史模型并集、健康/能力、持久化使用、Agent/压测摘要、模型到对话 URL 联动、目录降级和严格安全口径；`backend/docs/acceptance/2026-07-12-admin-model-operations-v1.md` |

## 不要重复建议

除非用户明确要求扩展、返工或复盘，下列方向不要再作为“下一步”主动建议：

- “做多模型真实能力矩阵 / 多模型测验增强”。
- “做模型目录巡检/同步机制”。
- “做 Search / Read Planner v1.1/v1.2/v1.3”。
- “做 SourceCandidateRanker 或 Evidence Ledger 最小版”。
- “把 Prompt / Agent 策略 / 模型展示配置落库”。
- “做 CI / 发布门禁 v1”。
- “把 Runtime Config 页面做成配置编辑器”。当前产品定位是只读观察面板，写操作走 Agent + 测试 + CI/CD。

## 当前开放方向

当前没有已确认的 P0/P1 基础设施优化项。新的下一步应来自明确产品目标或线上问题证据，例如：

- 用户明确提出的新产品能力。
- 线上真实场景暴露的 bug、性能问题或回归。
- 已有验收报告中的慢响应、失败模型或质量风险进入产品策略调整。
- 知识库、项目空间等新方向，但必须先做现状确认和计划。

## 2026-09-08 Issue #57：Agent 反馈与证据闭环（本地开发及审查完成）

- 工作分支 `codex/issue-57-agent-feedback`，应用基线 `21fefbcc`。失败/降级/成功分开累计，有实际网页证据的普通联网及领域工具恢复答案不再因缺引用被整段替换；无证据保留一次恢复后诚实未完成，产品/知识库/深研边界保留。
- 搜索反馈带 query 和可用日期/站点，模板只出现一次；去掉服务端阅读数量处方、媒体白名单、重复搜索守卫及死配置。共享来源身份键用于跨批引用及 distinct read，原始链接保留，历史编号/ID 兼容。
- 实际格式化 Observation 在已有工具日志中有界保存；每轮记录上下文裁剪后保留/移除的工具 ID。审查发现的默认线程池被占满的问题已修，复用现有轨迹专用线程和真实 worker 生命周期；迟到补写需刷新详情的限制保留并明确说明。
- 新固定证据质量回放器默认不调用模型，保存完整请求、代码与样本摘要及原始答案；引用编号正确不自动代表事实正确。两平台 CI 补齐全部 7 个本轮函数式测试入口。
- 本地完整后端 **4042 passed、4492 subtests、2 skipped**，前端 **2482 passed**，最终生产构建成功；Ruff/格式/ESLint/架构/diff/CI入口及部署脚本契约通过。tsc 25 条旧错误与未改基线逐字一致；独立审查发现的问题均修复复核，无本轮剩余可达 P0/P1。
- 公开 master 的 12 条真实模型基线包含归属错误及早期适配器协议限制，不能证明候选效果。候选代码传输 dev 临时目录被自动审批拒绝，待明确目的地授权；候选真实回放、push/PR/远端CI/合并/部署及自然登录页面验收均未执行。
- [实施计划](implementation-plans/2026-09-08-issue-57-agent-feedback.md)；[修复与验证报告](reports/backend/2026-09-08-agent-feedback-issue57.md)。旧固定中文收尾、产品供应商正则与长文窗口另行保留，未声明已完成全量回答质量改造。

## 2026-09-07 Agent 调用预算放宽（PR #52 已合并，dev 预算已核验生效）

- 发布补记：用户随后授权发布，master `38b29a8f`；17:01 accepted-release 与容器镜像一致，健康检查通过。实际 64 轮/200 调用/1800 秒、MCP 64、网络 40/100、Redis TTL 1920 已核验。此前未发布状态为阶段记录，详见预算报告。

- 用户明确授权放宽调用预算。dev `agent_strategy/default@2026-09-07.agent-budget-v2` 已激活：普通/研究搜索计划和搜索硬上限均为 40 次、网页读取 100 次；旧配置保留可回退。部署容器中的真实预算准备函数验证放行 40/100 次，未调用外部提供方。
- Agent 默认 64 轮/200 次工具/1800 秒，MCP 默认 64，Redis 锁与 meta 覆盖新时限，续跑使用当前预算；代码尚未 push/PR/合并/部署，线上全局限制仍是 8 轮/20 次/300 秒。dev MCP 部署变量 64 已准备，但容器仍为 8。
- 后端目标回归 218 passed + 67 subtests，部署/工作流契约 67 项通过；Ruff、架构、shell 与 diff 检查通过，独立审查无 P0/P1。通用工具失败恢复仍待实现，不把本次预算调整表述为该机制已交付。
- [预算调整与生效证据](reports/backend/2026-09-07-agent-call-budgets.md)；[实施计划](implementation-plans/2026-09-07-agent-call-budgets.md)。

## 2026-09-07 工具载荷和结果可观测性修复（已发布并完成目标真实验收）

- 用户指出 trajectory 入参/结果过度脱敏；确认日志先只保存审计摘要、详情再复用审计过滤。分支 `codex/trajectory-tool-details` 在既有 ToolCallLog JSON 元数据中保存有界业务快照，详情独立读取；凭据遮盖与截断分别标记，历史摘要和损坏记录明确提示。
- 不改工具执行、模型上下文、权限或数据库结构；原审计列表投影保留，正文列按需加载，避免批量查询增加负载。记录的是 handler 业务输入/返回，不是原始 HTTP 或格式化后的精确 Observation。
- 本地核心回归 155 passed + 79 subtests，工具执行关联回归 231 passed + 110 subtests（两组重叠 11 项）；前端 52 passed，Ruff/ESLint、架构、diff 与生产构建通过；独立审查无 P0/P1。
- 用户明确要求“发布吧”后，PR [#51](https://github.com/HyxiaoGe/fusion/pull/51) 从审查 HEAD `2cddede6` 合入 master，API/UI 部署版本均为 `f7880b2461239329edd89e6a32cb0591d93163a1`。[PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/34091804685)、[master CI](https://github.com/HyxiaoGe/fusion/actions/runs/34092432594)、[dev API → UI](https://github.com/HyxiaoGe/fusion/actions/runs/34092432979) 均 success；北京时间 15:00:58 发布完成，独立核对 accepted SHA、实际镜像与健康检查，重启数均 0。
- 原登录态 Chrome 中，新苏州天气载荷/四天结果可见且无脱敏/截断；自然搜索后读取 Python 官方文档，正文可见，超过 32768 字符仅标 `result.content` 截断。刷新详情文本完全一致；历史天气明确提示只记录了摘要。详情接口 200、console 无 error/warn；网页刷新中的单个模型请求取消和长 SSE 早期网络缓冲截断均如实记录。详见 [修复及发布记录](reports/backend/2026-09-07-trajectory-tool-details.md) 与 [实施计划](implementation-plans/2026-09-07-trajectory-tool-details.md)。

## 2026-09-07 Agent 停止轨迹补修（已发布并完成目标真实验收）

- 首批真实验收发现 Redis 停止后末轮取消事件丢失；在隔离分支 `codex/agent-loop-stop-trajectory` 补修，PR head `c61ffb58`、合并/部署 SHA `7a93c7fffb5108b0a0b5aa6b991a2cd62e78a00f`，PR [#50](https://github.com/HyxiaoGe/fusion/pull/50)。仅保留明确取消终态到该 Run 账本，重抛原所有权异常，LLM 详情只调度一次；未改消息写入权或历史数据。
- 本地 `375 passed + 173 subtests`，Ruff、架构与 diff 检查、独立审查通过；[PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/34084963675)、[master CI](https://github.com/HyxiaoGe/fusion/actions/runs/34085415461)、[dev API → UI](https://github.com/HyxiaoGe/fusion/actions/runs/34085415682) 均 success。13:14:19 部署完成；13:14:43 独立核对 API/UI accepted SHA、镜像、服务健康，重启均 0。
- 新自然天气对话 [1c5ef7b8](https://fusion.seanfield.org/chat/1c5ef7b8-99c7-4244-a193-05b9a29940e6)，Run `7751ec243378462181570a8d8ab3a75d`：真实工具卡片后停止，刷新卡片完全一致；23 条事件 sequence 0–22 连续，取消和 Run 中断均明确记录，末轮归因为 suppressed/none/no_content，轨迹 complete。最终部署后再次刷新与接口/console 检查通过。
- 普通输出、工具前抑制、服务端替换已在首批自然用例验证；工具执行中普通失败结果或计划快照被拒绝时仍如实降级，不声称所有停止场景都完整。多城市 clarification_only 与现有 product_guard 策略独立记录，未扩入修复。
- [完整补修与验收记录](reports/backend/2026-09-07-agent-stop-trajectory.md)。以下首批记录保留为问题发现时的历史阶段。

## 2026-09-07 Agent loop 首批 dev 发布（历史：真实验收发现停止轨迹缺口）

- 用户明确授权发布；PR [#49](https://github.com/HyxiaoGe/fusion/pull/49) 合入 master，部署 SHA `11906526a49907e70b8c0133342b5524e2c57faf`。
- PR CI [34071451174](https://github.com/HyxiaoGe/fusion/actions/runs/34071451174)、master CI [34071945145](https://github.com/HyxiaoGe/fusion/actions/runs/34071945145)、dev API → UI [34071945449](https://github.com/HyxiaoGe/fusion/actions/runs/34071945449) 均 success；前后端部署于北京时间 2026-09-07 09:17:21 完成。
- 独立核对 API/UI accepted SHA、容器 image ID/digest 均匹配；服务运行中且重启数均为 0；API healthy、数据库/Redis connected，UI/chat/new HTTP200，公网页面引用的 JS 已包含新归因界面。
- 解锁后复用已登录 Chrome，普通回答和真实天气工具路径覆盖 emitted/suppressed/replaced；聊天答案、工具卡片、原模型候选与归因刷新恢复通过。停止后的真实天气卡片也完整保留；但末轮取消事件因 Redis 冻结未进入轨迹，产生 finalize_mismatch，完整观测未通过，进入专项修复。
- 另记录多城市请求落入 clarification_only、product_guard 替换原模型建议的既有产品行为，不在本轮改动。会话/stream/轨迹/详情/stop 观察到 HTTP 200，console 无 error/warn；头像一度 408 后恢复，页面导航有请求取消；首轮网络缓冲截断已注明。
- [发布记录与查看方式](reports/backend/2026-09-07-agent-loop-dev-release.md)。下面的本地实现记录保留为发布前历史阶段。

## 2026-09-07 Agent loop 可靠终止与输出归因（本地实现与自动化验证）

- 从删除 PromptHub 的 `ba6e108a` 建立隔离分支 `codex/agent-loop-reliability`。保留本地 prompt、自实现循环和既有产品策略，不引入框架、数据库迁移或新观测服务。
- `6c5cd2c5`、`8009f090`、`c230a823`：服务端 checkpoint/取消/失败保留真实工具结果，客户端仍拒绝伪造结果；并行工具异常后等待取消清理，修复重复 cancel 打断异步 finally；三种预算触顶直接交付现有产品事实和未完成说明，不继续请求模型、工具或计划修复。
- `d9ad4387`：通过既有 LLM terminal、账本、详情 API 和 trajectory 界面说明模型候选已采用、已改写/替换、未采用或已撤回，并记录来源与原因。保留原候选，旧历史显示未知，无模型收尾不借前一轮身份；Linux/Windows 原门禁追加新增函数式 pytest。
- 主代理综合后端 `557 passed + 194 subtests`，前端五文件 `192 passed`，根 CI 契约 `67 tests OK`；改动文件 Ruff/format/ESLint、架构、shell 静态及 diff 检查通过，production build 成功。完整 tsc 仍有 25 处既有错误，与干净基线逐字一致，无新增错误。
- Task 1、Task 2 均通过独立规格与质量审查；最终全分支审查发现的单慢工具重复父取消缺口已在 `c230a823` 修复并定向复审通过，无剩余可达 P0/P1。
- 本条仅记录本地代码与隔离测试。未 push、PR、远端 CI、合并、部署或真实浏览器/模型/外部工具验收，保留分支和工作树供审阅。
- [规格](specs/backend/2026-09-07-agent-loop-reliability.md)；[实施计划](implementation-plans/2026-09-07-agent-loop-reliability.md)；[验证报告](reports/backend/2026-09-07-agent-loop-reliability.md)。

## 2026-08-26 主聊天系统提示词一期（本地实现/静态验证，未推送、未 PR、未部署）

- 统一本地可信段落组装；用户偏好不会替代基础规则或因标题碰撞抑制工具/计划规则；修复历史查询年份限制。
- 新增 `system_prompt_prepared` 成功/失败结果，以及每次主聊天/收尾请求的有效 system 指纹；只保存安全元数据，无准备中状态、无新表、无 Skills。
- 迁移前已核对 dev 生效模板；收尾摘要差异保留现有解析路径，其他 PromptHub 消费者不变。
- 独立审查发现并修复空文本加非图片附件回归；API 22文件目标集合 `416 passed + 121 subtests`，ruff、架构及 diff 检查通过。
- 前端 27 文件 376 测试及隔离构建通过；37 个既有类型错误与干净同 SHA 基线逐字一致，无新增。跨仓独立审查通过，无未解决 P0/P1。
- 草稿 PR：API [#60](https://github.com/HyxiaoGe/fusion-api/pull/60)、UI [#48](https://github.com/HyxiaoGe/fusion-ui/pull/48)。UI 首轮 CI 全量 2350 测试及容器构建通过；API 首轮 CI 暴露两份旧集成测试的查询夹具/事件顺序不匹配，修正后主代理以 unittest 重跑 46 测试通过，生产代码未因此改动。最新分支 CI 结果以 PR Checks 为准。
- 本条记录本地实现、代码验证与独立审查；分支 CI 以配套 PR 为准，未合并部署，未完成新版本真实浏览器/模型验收。协议和范围见 [一期契约](specs/backend/2026-08-26-system-prompt-assembly.md)。

## 2026-08-27 主聊天 Prompt Runtime v2（仅本地开发与静态验证）

- Run 初始 Prompt 改为 `app_identity → 固定运行规则 → current_date → user_preferences`，模板版本更新为 `2026-08-27.1`；稳定前缀不再被动态日期和偏好截断。
- 默认 Web、高德、FlyAI、Plan 工具仍向主 LLM 公告 schema，但 Run 初始 Prompt 不再因工具可用而注入整段高德/FlyAI 领域 Prompt；自然表达“我现在在北京，我想去上海，你可以帮我吗”仍保留 `route_compare`。
- 工具调用前规则下沉到 description/schema 和后端校验；高德事实约束继续随实际结果进入 ToolMessage，FlyAI 实际结果新增完整事实边界及单班次接驳后续规则。旧高德整段 Prompt 常量和两条初始注入 helper 已删除。
- Trajectory 节点明确显示“Run 初始系统提示词”，正文说明后续 Round 可追加语言、修复、研究或总结规则；LLM 请求指纹改标“当轮实际系统消息指纹”。
- 本地证据：API 全量 `2918 passed, 2 skipped, 828 subtests`，Ruff check/format 通过；UI 全量 `2371 passed`，目标 ESLint 和 production build 通过；两仓 `git diff --check` 通过。
- 本条没有提交、推送、PR、CI、部署、本地服务或真实模型/浏览器验收。协议见 [v2 规格](specs/backend/2026-08-27-prompt-runtime-v2.md)。

## 2026-08-27 Run 级能力路由（本地实现与自动化回归）

- API 在首个 LLM Round 前以 `RunCapabilityResolution` 冻结最小能力包；同一 resolution 原子派生外部 tool definitions、handlers、bindings、announced/final tools、`update_plan` schema 和条件 Prompt sections。服务端共享 capability contract 同时约束当前 Run、实时事件、durable ledger 与历史 DTO；`AgentSession.run_config.capability_resolution` 是刷新和历史事实源。
- 权限边界默认收窄：普通 direct/transform/clarification 不公布工具；Deep Research 只开放完整 search/read；禁用工具、无 function calling、Knowledge Grounded 和未满足必需工具时不把 schema 发给模型。已授权 MCP alias 的只读元数据只参与禁用/无 FC 路由分类，不复用受执行预算裁剪的 ToolSet，也不授信未授权 alias。
- 自动化行为 fixture 共 501 条，其中 491 条路由记录通过真实 `build_agent_loop_call_config()` 与 `prepare_agent_loop_messages()` 核对 package、definitions、handlers、bindings、最终工具与 Prompt sections，不使用静态 JSON 自洽替身。矩阵覆盖问候、身份、日期、联网、URL、地图、航班/铁路、混合行程、Deep Research、Knowledge Grounded、模糊意图、抽象流程、按名词类型区分的定义类稳定知识与时效查询、URL query/自然动作边界、页面内搜索、中英文显式/自然内置、产品与 MCP 工具 hard deny 及最终再授权、当前请求及同对象中英文作用域、回指对象、`go online`/访问网络等全局中英文联网禁用、未知交通方式 fail-closed、交通方式否定、逗号/冒号/破折号子句边界及实体内部短横线保留、产品子集筛选、否定疑问、领域/解释补语、天气地点补语和中英文时序连接词。
- 本地证据：最新目标集（含生产 wiring 与 lifecycle 指纹契约）`654 passed + 1065 subtests`，其中路由单测 `506 passed`、真实组装 fixture `491 subtests`；API 权威全量 `3489 passed, 2 skipped, 1895 subtests`，Ruff、任务改动文件 format check 与 diff check 已通过。能力包指纹现覆盖完整 resolution、announced tools、安全 MCP bindings、task/network/evidence policy 与 Prompt 模板版本；实际 Prompt snapshot/fingerprint 继续单独证明 section/body。UI 全量 `2430 passed`、production build、目标 ESLint 与 diff check 已通过；最终替换式对抗审查结论为 CLEAN。全量首次发现 Trajectory 列表读取整个 `AgentSession.config`，共享路径以 `7e49f5f` 收窄为 capability resolution 轻量投影后，原失败单项与全量均通过。
- 当前状态仅为 API/UI 分支本地实现和静态/单元回归；没有推送、PR、CI、部署、真实模型或登录态浏览器验收。协议见 [Run 级能力路由规格](specs/backend/2026-08-27-run-capability-router.md)。

## 2026-09-06 全局 Prompt Runtime P1 稳定 Section Identity（本地实现与自动化回归）

- 新增不可变内部消息类型 `PromptMessage(role, content, section_id)`；非 system 消息强制 `section_id=None`，system 组装、知识库上下文、续跑、研究阶段、产品结果、计划修正、工具回合和终局总结统一保留稳定身份。
- 工具契约、计划契约、深度研究契约、无联网/无图片边界、语言规则与终局控制清理均按 `section_id` 去重或删除；相同中文 marker 出现在其它身份正文中不会误删，Prompt 正文任意热更新也不会破坏控制逻辑。
- `to_provider_messages()` 成为 tokenizer、Agent 普通轮、终局总结和非流式聊天共用的 provider 投影；外部 payload 不含 `section_id`，语言规则在投影时恢复旧的 system 合并形态，保持模型实际消息顺序与正文边界不变。
- 本地证据：P1 目标集合 `220 passed + 67 subtests`；共享行为契约 `53 passed + 1057 subtests`；后端权威全量 `3926 passed, 2 skipped + 4353 subtests`；Ruff check、本次 33 个 Python 文件 format check 与 `git diff --check` 全部通过。
- 当前只完成隔离分支 `codex/issue-34-p1-section-identity` 的本地实现、提交和自动化验证；尚未推送、创建 PR、运行 CI、部署 dev 或执行真实模型验收。P2-P5、catalog 扩容、Prompt 英文化及输出侧 sanitizer 均未提前实施。

## 2026-09-06 Issue #34 P2 单 Run attempt 冻结（本地实现与自动化验证）

- 接手代码基线为 PR [#37](https://github.com/HyxiaoGe/fusion/pull/37) 合并后的 `3c737caf41399b7868bb0e8ec24da2a2a3d6249b`；现场核对 PR #36/#37 均已合并且 5 项检查成功。上文 P1 未推送记录是当时的历史快照；用户提供的后续 dev 与真实 Chrome 验收作为接手背景，本阶段没有复跑或补称为新验收。
- `PromptBundleSnapshot` 在分类前完整冻结模板，身份与新 Run 原子持久化；分类后只能补配置，已冻结的 Run 不得重入。分类准备失败/取消会落 Run 与 SSE 终态，身份写入失败不进入模型。
- `RunPromptSnapshot` 包含初始最终系统段落、身份和指纹；工具、总结、continuation 与语言策略使用同一冻结上下文。正文持久化仍可显式 degraded，安全只读接口可独立返回版本身份。标题和推荐问题每次独立冻结并携带自己的归因。
- 本地后端全量：`3943 passed, 2 skipped, 4359 subtests passed`；Ruff 与架构检查、改动文件 format、`git diff --check` 通过。验证包括切包、并发 task/thread、取消、原子身份失败、同 Run 重入拒绝与三个新 attempt 的 lineage；记录见 [P2 验证报告](reports/backend/2026-09-06-global-prompt-runtime-p2.md)。
- 分支 `codex/issue-34-p2-run-snapshot` 已推送并创建 PR [#38](https://github.com/HyxiaoGe/fusion/pull/38)。`66cd3cf9` 独立运行时复审无可达 P0/P1，模拟 runner 切包实验通过；后续补齐 Linux/Windows 容器入口的三个 pytest 冻结文件执行契约，目标 `667 passed + 6 subtests`。最终 HEAD 复审和 PR CI 单独核对；尚未合并、部署 dev 或做真实模型验收，P3–P5 尚未编码。

## 2026-09-03 Aug 27-28 重构评审整改（本地实现与自动化回归）

- issue #23：中英文城市白名单合并为 `backend/app/utils/location_names.py` 单一事实源，按行政区划整体收录，取代此前两处手挑的 26/41 条名单。端点未命中词表不再整体落入 `clarification_only`；强制调用路线工具的 `explicit_route` 判定维持原严格度。残留缺口（未收录专名与抽象名词在规则层不可分）已记入规格文档。
- issue #25：`repair_unsupported_product_answer()` 的实际改写由 `PRODUCT_ANSWER_REPAIR_ENABLED` 控制，默认关闭；校验判定与 reason code 保留，并新增 `product_answer_observability` 记录"本应被改写"的反事实。观测字段只有固定分类与布尔值，不含模型或用户正文。
- issue #26：删除 `normalizeTrajectoryEvent.ts` 中 package→工具/计划/日期/reason code 的四张契约表与两个语义校验函数（831 → 594 行），只保留结构性校验；未知 `package_id` 与 reason code 原样展示而不是丢弃整条 resolution，展示侧缺少 i18n 文案时退回原始 id。
- issue #24：`_classify_standard_request` 的 400 行 / 39 分支拆为信号层加三个决策层，最大函数降到 116 行；新增 `CapabilityClassifier` 协议与 `resolve_run_capability_route(classify_fn=...)` 接缝，规则分类器成为可替换的默认实现，骨架与契约校验不随分类实现变化。行为不变，规格验收矩阵全过。
- PR #27 评审整改（3 项 P1）：词表不再作为出行能力准入条件，结构化端点加明确出行动词即公开路线工具，抽象关系改由职业/流程/业务状态/机构职能四族端点语义挡住；地名数据改为 `(省, 中文, 英文)` 三元组，中英索引与城市 ID 全部派生自同一条记录，消除中英漂移；城市 ID 保留省级身份，`北京市朝阳区` 与 `辽宁省朝阳市` 不再撞键。
- PR #27 复审整改（2 项 P1）：出行准入改为要求端点带正向地点证据，不再依赖抽象词表反向判定（`从零到一`、`从MVP到PMF`、`从100万用户到1000万用户`、`产品从概念到上线` 均不再误路由到地图工具）；覆盖率改由补齐县级市数据解决，词表增至 782 条。英文别名改为元组加查询规范化，恢复 `xi'an`、`hongkong`、`macao`、`chiangmai`、`danang` 等既有别名；新增拼音撞车与别名元组形状的防退化测试。
- PR #27 三轮复审整改（2 项 P1）：新增不强制调用的安全能力路径——端点无地点证据但形状像专名时公开 `route_compare` 且不进入 `explicit_route`，恢复 issue #23「结构化起终点加明确出行动词即获能力」的原始验收（此前被改成词表准入并把失败写进了规格与 fixture，已改回）；形状判定只用字符类与长度，对新词泛化。修复裸市辖区被误认成同名地级市（`朝阳区` → 辽宁朝阳），下级行政区必须自带名称才归属上级城市。
- 本地证据：API 全量 `3586 passed, 1 skipped, 1943 subtests`，Ruff check 与改动文件 format check 通过；UI 全量 `2462 passed`，目标 ESLint 与 production build 通过。
- 本条没有 CI、部署、真实模型或登录态浏览器验收。

## 2026-09-02 Task 5 文档、台账与导航迁移（本地版本库）

- 根 `README.md`、`AGENTS.md`、`CLAUDE.md` 已建立导航；`docs/EXECUTION_LEDGER.md` 成为唯一执行事实源。
- 23 份后端规格和 14 份前端规格迁入 `docs/specs/{backend,frontend}`；仅保留两份未闭环前端计划到 `docs/implementation-plans/frontend`，两份前端报告迁入 `docs/reports/frontend`，其余 26 份旧插件计划与重复台账/计划已从当前树删除。
- `fusion-next-step` 收敛为根目录唯一 skill；八个后端专属 skill 保留并修正凭据、真实 dev 写入与单仓发布核验边界。结构合同已完成 RED→GREEN，`git diff --check` 与旧兄弟仓路径/固定测试凭据残留检查通过，业务目录无 diff。
- 本条是 Task 5 交付前的历史快照，只声明版本库内文档、导航和 skill 迁移的本地实现/静态验证；当时尚未注销旧 runner、退役旧 workflow、修改旧仓 README、归档旧仓，也尚未 push、创建 PR、合并或发布。当前终态以紧随其后的迁移收尾记录为准。

## 2026-09-02 Monorepo 迁移收尾（已合并并完成 dev 发布）

- PR [#18](https://github.com/HyxiaoGe/fusion/pull/18) 于北京时间 2026-09-02 08:11 合入 `master`，merge commit `a423b7f3`；Task 5 文档、导航与 skill 整合进入新仓。PR [#19](https://github.com/HyxiaoGe/fusion/pull/19) 于北京时间 2026-09-02 17:25 合入，当前收尾基线为 `fb4478d2`。
- 两次 `master` 发布均完成 API → UI dev 链路：Actions [33574259777](https://github.com/HyxiaoGe/fusion/actions/runs/33574259777) 与 [33614071343](https://github.com/HyxiaoGe/fusion/actions/runs/33614071343) 均为 `success`。新仓 repo-scoped Runner 当前为 `dev-server-fusion-monorepo` 与 `windows-build-fusion-monorepo-01`，两者均在线并同时带有 `fusion-api` / `fusion-ui` 应用标签。
- `HyxiaoGe/fusion-api` 与 `HyxiaoGe/fusion-ui` 当前均已归档；新仓 `HyxiaoGe/fusion` 已承接版本库、PR CI、镜像构建和 dev 发布入口。旧仓历史、PR、Actions run 与 release 继续留在归档仓供追溯。
- **已知外部遗留：** 两个旧仓的 Actions 权限仍为 enabled，`dev-server-fusion-api` 与 `dev-server-fusion-ui` 两个旧 Linux repo-scoped Runner 仍显示 `online`。因此不得把旧 runner/workflow 写成已退役；注销 Runner、禁用旧仓 Actions 需作为独立外部资源清理，在重新核对影响并获得授权后执行，不阻塞新仓当前 dev 发布链。

## 最近发布记录

| 日期 | 仓库 | commit | 内容 | 验证 |
|---|---|---|---|---|
| 2026-09-02 | `fusion` | `fb4478d2` | 重构 monorepo 对外 README 与项目说明，形成迁移后的公开入口 | PR [#19](https://github.com/HyxiaoGe/fusion/pull/19) 已合并；dev API → UI Actions [33614071343](https://github.com/HyxiaoGe/fusion/actions/runs/33614071343) `success` |
| 2026-09-02 | `fusion` | `a423b7f3` | Task 5 文档、唯一台账、协作导航与 skill 整合，旧仓归档后由新仓承接协作和发布入口 | PR [#18](https://github.com/HyxiaoGe/fusion/pull/18) 已合并；dev API → UI Actions [33574259777](https://github.com/HyxiaoGe/fusion/actions/runs/33574259777) `success` |
| 2026-07-13 | `fusion-api` / `fusion-ui` | 本次提交 | 对话上下文状态 v1：单轮上下文安全事件、失败/停止快照与 JSONB 刷新恢复；输入区紧凑剩余比例入口、实际/预计 Token、裁剪说明、暗色/窄屏/键盘与中英文支持 | 对抗式复审关闭累计 Token 误用、历史回退、按钮闪烁和错误态误导；后端 Ruff、架构检查、全量 `996 tests`；前端全量 `1118 tests`、目标 ESLint、production build |
| 2026-07-13 | `fusion-api` | `f5152be+b721edd+fc6ad22` | 长对话上下文治理第一阶段：单轮上下文遥测、生成约束、Token 预算感知 Context Manager、完整 turn/工具事务原子裁剪、结构化错误、SSE heartbeat 与安全生产阶梯 runner | 对抗式复审无剩余 P0/P1；Ruff、架构检查、全量 `983 tests`；Actions `29218910325` / `29224101606` / `29225925681`；生产运行 `perf-20260713-051915-9512b94b` 四档全通过，90% 档从 232,305 裁到 192,280、移除 1 turn/2 messages，费用 `$0.564035`、资源 0 restart/OOM、会话/Token 清理成功且如实记录 1 个账号行残留；真实登录态 Chrome 新会话 `868bee65-2e50-4a3f-b3e5-eb538394c859` 即时渲染/流式完成/标题/刷新恢复通过，刷新记录 1 条非阻断 React `#418` hydration error |
| 2026-07-12 | `fusion-api` / `fusion-ui` | `api:5624241+1a2b456 / ui:3097c6b` | 管理员模型运营中心 v1：只读 current/history/unknown 模型视图、健康与能力、持久化用量、Agent/压测摘要、详情 URL、模型到对话联动；目录失败退避、脏 ID 降级和安全投影 | 独立对抗式复审无阻断；后端 `977 passed + 98 subtests`、Ruff、架构检查，前端 `1085 tests`、ESLint、build；Actions `29190231438` / `29190141564`；生产真实登录态 Chrome 验证 19 个模型、历史/当前详情、模型筛选 18 条对话、浏览器返回与刷新恢复，console 0；`%2F` 生产探测受 Browser Control 限制并已如实记录 |
| 2026-07-12 | `fusion-ui` | `402644d` | 管理员审计中心 v1.4：补齐对话创建/更新时间；将 Tab、用户详情、用户筛选对话、对话详情和压测详情接入 URL/history，并修复手动筛选 URL 漂移、旧压测深链、焦点恢复与约 1200px 宽度回退 | 独立对抗式复审无 P0-P2；目标 `38 tests`、全量 `1056 tests`、ESLint、build；Actions `29186419597`；生产镜像 commit 对齐且 0 重启/OOM，真实登录态 Chrome 验证用户详情/用户对话/对话详情 URL，后退序列恢复 `1` 条用户对话与用户列表，日期北京时间展示，用户详情和压测详情刷新恢复，深链关闭不退出管理中心，console 0 错误警告 |
| 2026-07-12 | `fusion-ui` | `d2d473b` | 管理员审计中心 v1.3：用户详情从表格底部改为可访问弹窗，新增按用户查看对话的跨 Tab 自动筛选，并清理权限失效和普通 Tab 切换时的关联状态 | 独立对抗式复审关闭 403 残留和旧筛选复用问题且无新增 P0-P3；目标 `17 tests`、全量 `1043 tests`、ESLint、build；Actions `29184669641`；生产镜像 commit 对齐且 0 重启/OOM，真实登录态 Chrome 验证即时 loading、详情弹窗完全位于视口、关联用户对话 `1` 条、普通对话恢复 `997` 条，console 0 错误警告 |
| 2026-07-12 | `fusion-api` / `fusion-ui` | `api:d036d89 / ui:9d1075d` | 管理员审计中心 v1.2 安全与展示收尾：严格投影消息/文件/工具/Agent 数据，统一敏感参数脱敏和历史 schema 降级；同名用户补充唯一用户名与用户 ID，修复详情竞态、独立分页、压测语义与 CSP 导航边界 | 独立对抗式复审关闭签名 URL、客户端导航 CSP、错误分类和刷新竞态问题且无新增 P0/P1；后端 `968 passed + 98 subtests`、Ruff、架构检查，前端 `1036 tests`、目标 ESLint、build；Actions `29179213390` / `29179194679`；生产镜像 commit 对齐且 0 重启/OOM，真实登录态 Chrome 验证用户三元组、用户详情、对话消息/Agent/tool、压测 v2、刷新清空详情和访问审计，console 0 错误警告 |
| 2026-07-12 | `fusion-api` / `fusion-ui` | `api:64d3452 / ui:2e3d857` | 管理员审计中心 v1.1：压测列表收敛为元数据安全投影，详情重新校验脱敏协议；前端按需展示 L1-L4、资源快照和清理结果，完善错误/空态、北京时间、窄屏布局与 no-store | 后端 `961 passed + 87 subtests`、Ruff、架构检查；前端 `1026 tests`、目标 ESLint、build；Actions `29177299252` / `29177299457`；生产镜像 commit 对齐且 0 重启/OOM，真实登录态 Chrome 验证详情展开/收起/刷新恢复、窄屏无横向溢出、console 0 错误警告，详情访问审计已落库 |
| 2026-07-12 | `fusion-api` / `fusion-ui` | `api:7b24bda+56699e2+18047c4+ad8252e+2852f80+d4733d6 / ui:ef6817c` | 生产 L1-L4 完整压测：安全 runner、HTTP/SSE/恢复/停止/30 分钟稳态、资源硬门禁、管理员导入协议；并修复并发首次鉴权、Prometheus 缺样本、prompt cache 假场景和 L4 回调契约 | 后端 `954 passed + 87 subtests` 及最终目标测试、前端 `1018 tests` + build；Actions `29163183361` / `29161977007`；生产最终运行 `perf-20260711-182155-ca9da746`，L1 600/600、L2 28/28、L3 18/18、L4 60/60，数据库恢复基线，真实登录态 Chrome 导入/刷新/console 0 错误 |
| 2026-07-11 | `fusion-api` / `fusion-ui` | `api:1ec0f01 / ui:f2517ca+5e47692` | 管理员审计中心 v1：跨用户只读检索、消息/Agent/tool/文件元数据安全投影、压测汇总留存、访问审计、点击劫持防护与 hydration 收敛 | 后端 `893 passed + 69 subtests`、Ruff、架构检查、Alembic 单 head；前端 `1017 tests`、ESLint、build；Actions `29153889249` / `29154128135` / `29154607305`；生产迁移、权限 200/403、真实新数据、文件/工具、压测导入与清理后保留、刷新恢复、console 0 新错误 |
| 2026-07-11 | `fusion-api` | 本次提交 | 生产性能首轮基线与可复用 HTTP/SSE runner：一次性认证、脱敏结果、阶梯门禁、会话/令牌/agent step 精确清理 | runner `12 passed`、Ruff、format、compileall、生产确认 guard；API/源站/公网分层压测；真实生产 SSE 1→3→5 并发 9/9 成功；关键容器无重启/OOM，数据库恢复测试前计数 |
| 2026-07-11 | `fusion-api` / `fusion-ui` | `api:70f391d+6d05512+18fb967 / ui:d4f66a9+765974c` | 流式可靠性 P2：显式 `initial/continuation` 模式、严格 Redis 状态判定、共享可恢复流执行器、页面刷新恢复、stop guard/task CAS、部分输出原子持久化与取消终态收敛 | 前端 `985 tests`、`npm run build`、目标文件 ESLint；后端 `857 tests + 69 subtests`、Ruff、架构检查；GitHub Actions 与部署后真实登录态 Chrome 新会话回归纳入发布门禁 |
| 2026-07-11 | `fusion-api` / `fusion-ui` | `api:363233f / ui:e7d596d` | 流式可靠性 P1：自适应追赶、发送自动重连、Redis fail-fast/就绪检查、孤儿流终态和 task/message fencing；真实回归补齐标题模型输出预算 | 前端 `944 tests`、`npm run build`、目标文件 ESLint；后端 `806 tests + 69 subtests`、Ruff、架构检查；GitHub Actions 与部署后真实登录态 Chrome 新会话回归 |
| 2026-07-10 | `prompthub` / `fusion-api` | `prompthub:70b371f / api:PromptHub 接入提交` | 11 个业务 Prompt 迁入 PromptHub：published bundle、只读服务令牌、完整本地 LKG、shadow/apply 切换、版本观测与回滚门禁 | PromptHub SDK `70 passed`、backend Ruff/架构/Alembic 单 head；Fusion `741 tests OK`、Ruff/架构；CI/CD、真实 dev shadow/apply 与登录态 Chrome 回归 |
| 2026-07-03 | `fusion-api` / `fusion-ui` | `api:aae8e87 / ui:c9d6eda` | 会话资料/文件体验 v1：同会话资料面板、资料复用、文件权限校验和历史附件元数据保真 | `.venv311/bin/python -m pytest test/test_file_service.py test/test_chat_service.py test/services/chat/test_message_builder.py -q`、`/opt/homebrew/bin/ruff check app test`、本次改动文件 `ruff format --check`、前端 `npm test`、`npm run build`；CI/CD 和真实 Chrome 回归待本次 push 后完成 |
| 2026-07-03 | `fusion-ui` | `ea94879` | 运行时配置页收敛为只读观察面板 | `npm test`、`npm run build`、CI/CD `28647885300`、真实 Chrome `/settings` 回归 |
| 2026-07-03 | `fusion-api` | `24601de` | CI 指标推送改走 nginx 9094 鉴权反代 | GitHub Actions / dev 发布门禁 |
| 2026-07-02 | `fusion-api` | `3b0b627` | 实现多模型真实验收矩阵 | `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`，`backend/reports/model-acceptance/report-20260702-080341.md` |

## 下一步建议前检查清单

1. 按用户主题查本文件的相关记录。
2. 对照当前源码和相关 Git 历史，信息不足时再扩大范围。
3. 需要设计背景、模型验收或发布状态时读取相应文档与当前证据。
4. 回答当前决策，并说明与既有工作的关系；没有高置信方向就说明证据不足，不填充固定模板。

## 2026-09-06 Issue #34 P3a：完整 v2 LKG 与部署前桥接（本地验证，待 PR 复审）

- 从 `origin/master@ed3a789365a46c5724420d2ffec18d6f06040e81` 建立独立分支；用户定稿 v1 逻辑放弃、物理暂留，不推断旧字段，不实现 runtime v1 fallback。
- 从原始 published variables/正文重算官方 source revision，新增本地 schema 2/catalog/checksum；同 revision 冲突不覆盖，shadow 不取消 active，差异相对锁内 active 计算。新物理键 `prompt_bundle/fusion:v2` 不改历史 P2 Run 身份。
- 发布前用核验旧 image ID 和原 Prompt 配置冻结基线，再由候选 oneshot 原子预置 v2/追加回执，失败在服务替换前终止。首次发布保留 v1 active；旧 worker 和旧代码回滚锚点退出后另行停用，绝不删除正文。
- 独立工作树复审修复三项问题：提交后访问过期 ORM 对象、shell 配置被 Docker env-file 误解释、停止旧容器无法抓取基线。默认 SessionLocal 事务、三种容器状态、身份变化与各失败路径均有测试。
- 本地全量 pytest `3983 passed, 2 skipped, 4371 subtests passed`；unittest `3044 tests, OK (skipped=2)`；仓库级契约 `67 tests, OK`；Ruff/改动文件格式、架构、shell 与 diff 检查通过。测试使用 mock HTTP/模型、SQLite 和 fake Docker，没有启动本地服务。
- 本次授权范围仅到分支、独立 PR 和复审；未合并、未部署、未写 PromptHub、未改 GitHub 变量、未删除数据。P3b hold 与 P3c 引擎桥接另阶段实现，不能把本条视为全部 P3 或真实环境验收完成。
- [定稿实施计划](implementation-plans/2026-09-06-global-prompt-runtime-p3.md)；[P3a 迁移与验证报告](reports/backend/2026-09-06-global-prompt-runtime-p3a.md)。

## 2026-09-06 Issue #34 P3b：持久 hold、审计与回滚保护（本地实现与自动化验证）

- 在 P3a `90962a5c05368d6fbe038c95de6de5bc59da310a` 上建立独立分支 `codex/issue-34-p3-persistent-hold`。管理员进入/解除 hold 时，完整 v2 激活、持久 current-state 与追加审计在同一事务内提交；重复请求幂等，普通用户和伪造 actor 被拒绝。
- 同步在原 advisory lock 内直读 hold；held 期间只保存通过完整性和 P0 校验的 inactive 候选。数据库触发器保护 held 目标和审计历史，代码回滚后的原 P3a 同步器也无法越过保护。PostgreSQL 路径要求 READ COMMITTED；SQLite 测试不替代真实 PostgreSQL 并发验证。
- 发布器向目标镜像注入当前 preflight，验证目标真实完整包读取与冻结。hold schema 启用后，正向与失败回滚目标至少支持完整 v2；held 冒烟验收使用持久目标和真实 freeze，远端故障另记状态。保留 v1 和历史 Run，不执行 schema downgrade。
- 本地全量 pytest `4031 passed, 2 skipped, 4381 subtests passed`；unittest `3044 tests, OK (skipped=2)`；根目录契约 `67 tests, OK`；Ruff、改动文件格式、架构、shell 静态与 diff 检查通过。旧 P3a 同步器、新旧 revision、独立 worker 重启、陈旧缓存、真实脚本拼接和远端超时均有回归。
- 本阶段仅为本地实现与提交，当前 HEAD 独立复审另行记录。P3a 分支已推送，但创建 PR 被自动审批拒绝，具体 GitHub 外发确认尚未收到；P3b 尚未推送或创建 PR。未合并、未部署、未写 PromptHub、未改 GitHub 变量、未删除数据。P3c 桥接与 Jinja2 收口仍待独立实现。
- [P3b 验证与回滚报告](reports/backend/2026-09-06-global-prompt-runtime-p3b.md)。

### P3b 当前 HEAD 复审整改

- `3a705b66` 独立契约复审发现非 apply 模式可进入不被真实消费的 hold，以及 preflight 后进入 held 的模式转换竞态。enter 已要求 apply；hold schema 启用后受管理的所有部署/回滚目标始终要求 apply，不按检查瞬间的 following 状态放宽。
- 新增负向先复现四项失败，整改后目标 `52 passed`、后端全量 `4036 passed, 2 skipped, 4381 subtests passed`，Ruff 与 diff 检查通过。未执行任何环境操作或外发。

## 2026-09-06 Issue #34 P3c 独立引擎桥接（本地实现与自动化验证）

- 基于已通过两名独立代理 exact-HEAD 复审的 P3b `97da2d226b2d910bf76428fb8c23d12f25833d47` 建立 `codex/issue-34-p3-jinja-bridge`。完整旧 v2 原样保留，新 Jinja2 完整包有独立 catalog 与 P0 原字节基线；冻结 format/engine，四处模板消费者使用同次解析元数据。
- 追加持久 legacy → bridge → jinja2 转换事实及数据库激活保护，禁止跳过桥接或退回旧阶段。发布器按持久阶段执行全部引擎的真实校验/冻结/渲染探针；阶段提升与发布/回滚共用 fusion-dev 串行协调，验证 worker 和独立镜像锚点后才提交。
- 独立复审发现并修复未 attested 提升/激活、遗漏可自动恢复旧容器，以及 API 实际未配置 Docker HEALTHCHECK 的问题；已加入失败时不改变 active、不追加阶段事件的负向。基线提案工具仅从完整原始 published 导出材料生成文件，不写 PromptHub 或数据库。
- 本地全量 pytest `4072 passed, 2 skipped, 4389 subtests passed`，unittest `3044 tests, OK (skipped=2)`；根目录契约 `67 tests, OK`；随后两项追加策略回归与 CI 契约目标 `19 passed`。Ruff、改动格式、架构与 diff 检查通过。
- 未推送、创建 PR、合并、部署、访问真实 PromptHub 渲染端点或启动服务。P3a PR 创建仍被自动审批阻止，具体外发确认未收到。该条只记录独立桥接代码，最终 Jinja2-only 收口版本另行实现；真实往返、PostgreSQL 并发和多 worker 验收另列环境门禁。
- [P3c 桥接报告](reports/backend/2026-09-06-global-prompt-runtime-p3c-bridge.md)。

## 2026-09-06 Issue #34 P3c 最终收口（本地实现与自动化验证）

- 基于独立桥接提交 `9fc318b693bb7a55fc70577708e5e32ed19f815d` 建立 `codex/issue-34-p3-jinja-only`；P3a `90962a5c`、P3b `97da2d22` 和桥接版本均已通过两名独立代理 exact-HEAD 复审。最终树只接受 Jinja2，移除旧运行时渲染与桥接专用转换工具；四项默认模板保持桥接 Jinja 基线原字节，历史 P2 Run 身份与正文不改写。
- apply 启动要求完整 LKG、P0 原字节门禁和持久 jinja2 阶段同时成立，legacy/bridge 不放行。发布探针执行持久阶段要求的全部引擎契约，最终镜像不能跳过中间桥接部署。完整旧 v2 负向夹具保留原始材料并固定哈希，数据库审计和旧包继续保留。
- 最终全量 pytest `4071 passed, 2 skipped, 4389 subtests passed`；unittest `3045 tests, OK (skipped=2)`；仓库级契约 `67 tests, OK`；Ruff、架构与 diff 检查通过。两名独立工作树复审无可达 P0/P1，另有 53 项独立目标测试及 SQLite 启动阶段验证；正式提交后再核对 exact HEAD。
- P3a 已推送但 PR 创建被自动审批拒绝，具体外发确认尚未收到；其余三阶段保持本地。未创建 PR、合并、部署、启动服务、迁移真实数据库、写 PromptHub 或修改 GitHub 变量。真实 PromptHub 往返、PostgreSQL 并发、多 worker 收敛和新 Run 验收仍是后续环境门禁。
- [P3c 最终报告](reports/backend/2026-09-06-global-prompt-runtime-p3c-final.md)。


## 2026-09-07 工具失败恢复（发布与真实验收完成）

- 原失败Run `459accfa1afb463a98e6e8285570713e` 已确认非预算问题；修复上游错误保留、替代工具公告、失败后继续与无证据incomplete。预算发布不替代本任务。
- PR #53 合并为 `a1f861ec`，master CI `34108696960`、dev发布 `34108697319`全部成功；API/UI台账和运行镜像匹配，健康检查通过。
- 原样重跑香港周五旅游三天天气，真实Run `a490233a363a4f249399fe1f76142683`：高德UNKNOWN_ERROR→搜索成功→读取天文台成功→带来源的9月11–13日回答；4轮3工具，67条完整轨迹。刷新后回答、引用、工具链保留，业务API 200、控制台无error。
- 完整本地后端3035项、前端轨迹218项与构建通过；实施、真实来源核对及网络边界详见 [工具失败恢复报告](reports/backend/2026-09-07-tool-failure-recovery.md)。


## 2026-09-07 LLM搜索策略四项改造（已发布并完成真实回归）

- 多角度并行/分批查询、结果获取与候选筛选、证据不足继续与运行级引用、LLM控制count及搜索策略一起实现。默认10条，模型可请求1–20；移除普通最多两次提示、旧意图3/5数量及未指定recency时默认一周限制。
- 完整后端3049项通过（跳过2），前端引用64项通过，Ruff和差异检查通过，独立审查P1已修复。分支 `codex/llm-search-strategy`。
- PR #54/#55/#56均已发布；API `4eeabc37`、UI `21fefbcc`，CI/CD成功、镜像与账本匹配。真实投资三批五查询46条原始命中、39个去重来源，空正文失败后继续检索；Yahoo失败后自动找到并读取完整替代报道。天气高德及多个网页失败后完成9月11–13日回答（10轮17工具171秒），完整轨迹保留。
- 回归发现并补齐空正文误判、长导航挤占正文和12条摘要丢引用三项缺口。最终原会话刷新后投资20已用/19候选，裸数字与[U1]残留为空，引用12正确定位；天气刷新与引用正常，业务API200，控制台无新增error。完整后端3058项与前端2478项、前端生产构建通过；第三方favicon失败和既有独立tsc问题如实记录。详见 [搜索策略报告](reports/backend/2026-09-07-llm-search-strategy.md)。

## 2026-09-16 Issue #30、#57、#58 遗留代码修复（本地验证完成，进入 PR）

- 基于 `master@54c0f510`，分支 `codex/issue-closure-fixes-20260916`。#30 总结事实守卫使用冻结能力与实际内容证据；失败、空来源、只有 URL 的元数据不能放行外部事实。
- #58 推荐生成增加应用层截止时间，SSE 尾部覆盖生成预算；前端等待本消息 SSE 推荐事件，断流后有限轮询；生成失败记录 failed，终态后取消不再重复写 interrupted。
- #57 删除误改品牌/真实地名的逐供应商正则；三条通用安全兜底使用配置内多语言模板，按冻结的原始请求选择语言，并保持 SSE、持久化与 incomplete 一致。语言选择失败且尚无可用语言时保留默认中文，不宣称全系统国际化。
- 后端相关 26 文件 `491 passed、155 subtests passed`；最后辅助模型预算调整后定点 `24 passed、15 subtests passed`。前端相关 `177 passed`、生产构建通过；Ruff、目标 ESLint、架构和 diff 检查通过。独立 tsc 的 25 条错误与未修改基线相同；独立交叉审查无遗留可达 P0/P1。
- 当前授权到提交、推送、创建 PR 与 CI；不包含合并、部署或关闭 issue。真实推荐事件送达、语言选择准确率与 #57 回答质量仍需目标环境验收，不能由本地测试替代。详见 [修复与验证记录](reports/backend/2026-09-16-issue-closure-fixes.md)。
- 已创建 PR #72。首轮前端 CI 通过，后端 3187 项 unittest 的唯一错误来自提示词捕获夹具未断言新的推荐生成异常；补齐异常类型与原始原因断言后，本地相关五文件 45 passed，Ruff 与格式检查通过，等待补充提交的远端全量 CI。
# 2026-09-22 动态工具发现：dev 发布准备

- PR #111：默认关闭的发现路径，显式请求 option 开启；不是账号灰度门禁。工具仍限服务端授权目录，普通请求保留包分类；共享无证据交付守卫覆盖部分旧路径 deferred 回答。
- P08 reasoning 修复独立离线复核通过：13 条 P08、118 条相关回归；正式 stream 捕获不安全输出未泄露，SQLite 新 Session 读回只有安全 text，内部协议 reasoning 保留。假工具和真实模型配对试验不等于真实供应商验收。
- 首轮 API CI：unittest 运行 3266 条、OK（跳过 1 条）；额外 pytest 835 通过、1 失败，定位为发现目录模型指令内嵌 Python。已按原文移入 runtime_prompts.toml，未豁免检查；本地模板与发现回归 46 passed。部署需修复后的 PR required gate 通过。
- 真实 HTTP/SSE、供应商、Redis 恢复、取消刷新仍未完成；最终 PR/部署 run 与镜像身份见 PR #111 及部署机发布台账。本条不提前宣称已部署或完整验收。

## 2026-09-23 停止请求无响应与主干 CI 补救

- #115 合并后 dev UI 运行身份为 `1ba16d7`。复用现有 Chrome 标签拦住原 Run 的 `/stop` 请求，页面有界显示“停止结果未确认”；下一轮正常完成。原请求未送达，不能将原 Run 随后的 interrupted 归因为这次停止。刷新后原 assistant 占位与即时提示均消失，用户要求补齐刷新反馈。
- #115 合并提交的 master CI `35809108311` 因模型详情按钮异步出现导致测试失败。#116 将查找改为等待加载，PR CI 通过，合并为 `a7390ac5`；master CI `35812541016` 与 dev 发布 `35812541311` 均成功。API skipped，UI 台账 SHA、digest、image ID 与运行容器一致。
- 刷新提示 PR #117（`81374407`）合并为 `46670c03`；master CI `35816836058`、dev 发布 `35816836461` 成功，UI 台账与容器一致。原 Chrome 恢复流停止请求定向无响应后，页面有界显示未确认，刷新后提示保留，原 Run 仍运行。
- 首次补漏 #118（`bb133773`）合并为 `f818bbd9`，master CI `35818230176`、dev 发布 `35818230453` 成功，镜像身份一致。真实复验中第二次 `/stop` 返回 200、原 Run 中断，但 SSE 先释放控制器使页面停在“正在确认停止”，旧横幅刷新才更新。#119（`520b7cf6`）合并为 `a8872c13`，master CI `35819833962`、dev 发布 `35819834512` 成功且镜像一致；再次真实复验仍见第二次停止 HTTP 在 500 毫秒被客户端取消，API 200、原 Run 中断，页面当前页未收口。
- 超时核实补漏 #120（`d1e8e1f2`）目标 89 项与构建、PR CI `35820731969` 通过，合并为 `e9ccebd7`。master CI `35821169700`、dev 发布 `35821170708` 成功，UI 台账 SHA、digest、image ID 与容器一致。原 Chrome 标签再次复现首次停止无响应、刷新保留未确认提示；第二次停止 XHR 在客户端取消，API 返回 200，页面无需刷新即显示原 Run 已中断且旧提示消失，再次刷新仍正确。本轮停止体验页面验收通过。默认动态工具发现仍未切换，原因与逐项迁移条件见 [后续核实报告](reports/2026-09-23-issue71-next-phase.md)。

## 2026-09-23 查证来源证据策略与终局正文保留

- PR #123 将明确查证请求的正文与引用门禁独立为 `verified_web_v1`；PR CI、master CI 和重跑后的 dev 发布成功，运行容器内只读断言通过。策略仅在显式动态发现入口启用，默认页面仍走旧 `verified_web` 包。
- 首次真实页面回归发现旧包虽完整读取两份 PostgreSQL 官方文档，终局总结却移除全部工具消息，最终回答误称正文被截断。PR #124 保留查证总结中的已格式化工具正文，目标测试及 PR CI 通过；合并为 `10a95e7e` 后 master CI `35848263337`、dev 发布 `35848263945` 成功。dev API 台账、API/worker image ID 一致，健康接口 200。
- 同一 Chrome 标签原样重发查证请求：最终模型保留 5 条工具消息，回答按已读正文引用 COMMIT/ROLLBACK 官方页；刷新后回答、引用与完整轨迹保留。该默认查证路径的目标回归通过；显式动态发现路径尚无真实模型端到端验收，不能据此切换默认入口。详见 [查证来源验收报告](reports/backend/2026-09-23-verified-web-evidence.md)。

## 2026-09-23 近期改动分层回归（本地修复待发布）

- 固定种子 `20260923`：独立盲测 47 条纯规则兜底接受 18 条；94 条措辞扰动中 4 条规则包从可接受变为澄清，混合分类复核这 4 条均正确。dev 混合分类 15 条接受 14 条，剩余 `mcp_alias-02` 误判澄清。工具发现与已部署查证策略各 200 组合均无不变量失败；慢写停止测试独立重复 10 次通过。各层样本和结论边界见[分层回归报告](reports/backend/2026-09-23-seeded-regression.md)。
- 真实页面停止 Run `167f82d5c0554ed88896d8dc45479416`：当前页面和刷新后均已中断，但轨迹 `finalize_mismatch` 降级。数据库确认序号缺 #53；普通计划事件在停止后被 Redis 拒绝却预占序号，`llm_round_cancelled`/#52 与 `run_interrupted`/#54 已落库，非 `recorder_timeout`。
- 本地修复仅在普通事件被明确拒绝所有权时回退序号；取消终态的旁路序号仍保留。回归先失败后通过，相关 157 项测试与 28 项子测试、Ruff、格式、差异检查通过；独立代码审查未发现 P0/P1。修复未部署，真实修复版页面验收仍待发布后进行。

## 2026-09-23 PR #126 发布后的首轮功能回归

- #126 合并为 `6256541a`，master CI `35857965977` 成功；dev 发布 `35857966351` 首次 UI 烟测受登录弹层阻挡并回滚，重跑失败作业后 API/UI 发布成功。API/UI 台账和运行镜像身份与该合并提交一致，API 健康接口 200。
- 复用现有登录态 Chrome 标签完成 5 次自然语言请求、4 个会话。直接回答与上下文续问两条 Run 完成；PostgreSQL 查证为部分完成；Python 文档显式 URL 查证完成但未读到目标小节完整正文；停止 Run 最终中断。5 条 Run 轨迹均 `complete` 且序号连续，停止尾事件 `llm_round_cancelled`/#110、`run_interrupted`/#111 相邻，刷新后仍显示已中断、轨迹完整。
- 首次停止点击没有服务端 POST 记录，页面显示“停止结果未确认”且后台继续运行；刷新后第二次停止 POST 200 才真正中断。PostgreSQL 回答声称两次原文抓取失败，但事件只有 `web_search`、无 `url_read`。因此 PR #126 的轨迹缺口目标通过，整轮功能回归不能判为全绿。详细样本与界限见[发布后首轮功能回归报告](reports/backend/2026-09-23-postmerge-functional-regression.md)。

## 2026-09-23 第二轮工具事实与动态发现配对回归

- 更正上轮工具账本口径：原查询只覆盖完成侧事件；重新读取原 PostgreSQL Run 的全部 69 条连续事件，只有一次 `web_search` 的 started，没有 `url_read` started。最终回答原文称两次抓取无内容，确认这条工具事实自述与事件不符。
- 同一 dev、同一登录态 Chrome 标签重复两次 403 读页：目标 `url_read` 两次均 started 且降级，回答均准确描述目标失败及辅助搜索／首页读取；没有在这 2 次复现虚构调用。同一 PostgreSQL 原句成对走显式动态发现和普通旧路径，各 1 次，两者均实际读取 COMMIT／ROLLBACK 原文并给出来源回答。
- 发现路径这次的 `requires_catalog_evidence=false`、`evidence_policy=standard`；代码也把实验适配器的该标志固定为 false。因此配对成功不能证明门禁能阻止工具事实编造，更不能据此切换默认入口。没有修改产品代码或阈值。详见[第二轮回归报告](reports/backend/2026-09-23-second-round-tool-claim-regression.md)。

## 2026-09-23 Issue #127 来源证据补口与配对回归（待发布）

- 代码定位：发现路径的 `requires_catalog_evidence` 仍固定为 false；既有 `verified_web_v1` 可用实际 `url_read` 正文和引用约束交付，但“官方文档”未触发该策略。本地只扩充该明确来源信号，并加入原 PostgreSQL 编造反例、部分成功及全成功测试；不改默认入口、旧 Skill 或产品答案校验器。
- 同一 dev 的完整 `agent_events` 与页面配对：两条 `.invalid` 全失败、COMMIT 成功加另一页失败两组，发现与旧路径均有对应 started／完成事件，未对失败页给原文结论；旧路径另复现 COMMIT 未 started 却宣称两页均已直接读取。全成功目标页只取得单臂成功，另一臂遇到真实抓取降级；补测还有一次模型生成错误，因此尚无两臂全成功且来源映射清楚的配对证明。
- #107 原句同轮配对：发现路径天气工具成功但漏答身份，旧路径答身份却未调用天气工具且误写“北京 7 月”；#107 不关闭。本分支尚未合并或部署，真实样本运行的是旧代码。逐 Run、序号、页面原文、失败分类和本地检查见[详细报告](reports/backend/2026-09-23-issue127-source-evidence-regression.md)。

## 2026-09-23 #128 合并与 Issue #129 否定词边界（待发布验收）

- 用户转述 Claude Code 审查结论后，#128 的 PR 描述已明确其仅扩充“官方文档”来源信号、`requires_catalog_evidence` 仍为 false；PR API/UI/安全与必需检查全绿。#128 已合并为 `e1f3c7f5`；主干 CI `35880005394`、dev 发布 `35880006479` 成功。只读核对 API/UI 台账 `current_sha=e1f3c7f5`，API/worker 运行 image ID `sha256:ae5f7fcc8977c6d3356160ae13efefc4147859c8adac5ab784f31feec6cfced0`、UI 运行 image ID `sha256:54d9db201af08e4f4d6d652ea553acb4d0ef8a0a8704e7f2402611a59ca5cee1` 均与台账一致。#127 主问题仍开放，不能由部署身份推断查证页面或切换条件已通过。
- #129 基线独立复现 11 条“分别／个别／特别／区别”等普通词触发撤权，两路径同受影响。候选修复收窄裸「别」和工具名禁用的跨词匹配，真否定继续硬约束；新增目标测试首批修前 77 failed、10 passed，补带 URL 路由断言后修后 92 passed。原 33 条 rules 盲测修前后均 14/33，完整 47 条均 18/47 且逐条输出无变化。
- 候选尚未发布，旧路径与发现路径“分别打开两个真实 URL”的页面成功读页待验；详细分类、正反例与逐条盲测输出见[报告](reports/backend/2026-09-23-issue129-negation-boundary-regression.md)。

## 2026-09-24 PR #130 复审：否定「别」的封闭前置规则

- 初版按复合词尾字排除“别”被 Claude Code 复审指出表外漏网；六句原文与额外 12 个“X别”词已纳入反例矩阵。改为句首、分隔符及封闭祈使前缀白名单，用户授权扩充常用真否定前缀，以避免“我们别打开”“麻烦别访问”“这次别联网”等漏拦。
- 本地目标测试 249 passed，受影响路由/授权/发现测试 985 passed、21 subtests passed；另 2 项因本机监听限制与 SQLite JSONB 限制未通过，待 PR CI 验证。#128 基线 352 条禁用表达逐项零差异；原 33 条盲测 14/33、完整 47 条 18/47 且逐行零差异。47 条没有 ② 类授权否定样本，此缺口单独记为 #131，PR #130 不改 fixture。
- 封闭前缀不能覆盖任意主语：“小王别打开这个网页”在 #128 会禁用、当前候选不禁用，需在合并审查中明示。候选尚未合并或发布，两个入口真实页面成功读页仍待发布后验收。

## 2026-09-26 语义正则迁移第一阶段（已部署，API 验收发现回答缺口）

- 请求文本上的计划工具、搜索意图、火车类型和网络否定词匹配改由现有模型输出结构化约束，服务端继续校验并裁剪执行边界；动态发现仅在首次 `tool_search` 声明后冻结授权目录。
- 本地 3242 项 unittest 通过、2 项跳过；PR [#148](https://github.com/HyxiaoGe/fusion/pull/148) 合并为 `676c9fed`，dev 工作流 `36210514071` 成功，API/UI 发布台账与运行镜像匹配。五条真实 API 请求确认能力包与工具禁用边界；官方 PostgreSQL 两页正文和最终引用编号逐一核对。禁网天气回答仍给出未查询的典型气候数值，后续补修；页面未验收。详见[阶段一报告](reports/backend/2026-09-26-semantic-regex-migration-stage1.md)。

## 2026-09-26 语义正则迁移第二阶段（已部署，API 验收发现误拦和地点改写）

- 第一阶段已发布；第二阶段 PR [#149](https://github.com/HyxiaoGe/fusion/pull/149) required gate 全绿，合并提交 `68c5c96a`，dev 工作流 `36211131975` 成功，API/UI 台账与运行镜像匹配。
- 产品答案的主观措辞判断改由现有最终回答模型处理，保留可直接核对的硬事实边界及事实兜底。与第一阶段组合后 3245 项 unittest 通过、2 项跳过，CI 额外 241 项 pytest 通过。
- 提示词层探针与真实 API 分开：真实天气、车次、空铁、地点用例发现天气跨日期汇总被事实校验误拦、正确车次比较被残留票价词面规则误拦、空铁模型价格写错时被硬事实校验正确兜底、地点空白查询被改成 OR 导致不相关结果。已在后续补丁中针对误拦与查询改写修复，仍待该补丁发布复验；页面未验收。详见[阶段二报告](reports/backend/2026-09-26-semantic-regex-migration-stage2.md)。

## 2026-09-26 语义正则迁移第三阶段（已部署，API 复验仍有缺口）

- 基于前两阶段真实 API 发现的回答与查询缺口，调整无来源事实提示词、天气回答日期范围和地点搜索空格处理；票价词面误拦改为与已返回候选逐值核对。独立复审提出的缺价候选、错误比较和预算冒充票价等反例已纳入回归。3250 项 unittest 通过、2 项跳过，另 241 项 pytest 通过；PR [#150](https://github.com/HyxiaoGe/fusion/pull/150) 合并为 `cb76030b`，dev 工作流 `36213168754` 成功，API/UI 台账与运行镜像核对一致。四条真实 API 原句复验中，禁网天气不再给无来源数值；天气和高铁虽有正确模型候选，却仍被事实校验误拦，地点全市查询及未验证商圈推测仍存在。下一补修继续，原有 Chrome 页面未验收。详见[阶段三报告](reports/backend/2026-09-26-semantic-regex-migration-stage3.md)。

## 2026-09-26 语义正则迁移第四阶段（已部署，API 复验仍有天气缺口）

- 第三阶段真实 API 暴露的天气／高铁误拦与地点推断缺口进入最小补修。高铁原始模型末轮和持久化结果块已在本地用新校验器重放通过；独立复审提出的行政区豁免、疑问句与差价反例已加入回归。本地架构、Ruff、格式与差异检查通过，3253 项 unittest 通过、2 项跳过，额外 241 项 pytest 通过；PR [#151](https://github.com/HyxiaoGe/fusion/pull/151) 必需门禁全绿，合并为 `188a7e7e`，dev 工作流 `36214718672` 成功，API 台账与运行镜像一致。真实 API 高铁回答已直接给出正确比较；天气正确模型候选仍被事实校验误拦；地点首搜仍全市、附近查询失败但最终未编造步行距离。天气补修进入第五阶段，页面待验。详见[阶段四报告](reports/backend/2026-09-26-semantic-regex-migration-stage4.md)。

## 2026-09-26 语义正则迁移第五阶段（已部署，API 复验仍误拦）

- 第四阶段天气 run `22521c1aacd94afd856188db6179d389` 的模型候选与结构化结果逐句核对，定位风力范围长横线、高低温词组、疑问式限制语和“气温和风力”误匹配。新校验器已在本地用该原始候选重放通过；独立复审补充逗号／问号疑问句、肯定断言混入与显式最高温反例并修复。3254 项 unittest 通过、2 项跳过，额外 241 项 pytest 通过；PR [#152](https://github.com/HyxiaoGe/fusion/pull/152) 门禁全绿并合并为 `94ebb25a`，dev 工作流 `36216010145` API 部署成功且台账与镜像一致。同句 run `c87c9b82f6f5465b998c671ce82cdf1f` 的新模型候选仍因疑问范围误判而退回多日兜底，后续继续补修；页面待验。见[阶段五报告](reports/backend/2026-09-26-semantic-regex-migration-stage5.md)。

## 2026-09-26 语义正则迁移第六阶段（已部署，天气误拦仍存在）

- 第五阶段真实候选“因此无法确认上午是否下雨”被当作肯定降雨，收窄疑问范围后本地重放通过，独立复审未找到本轮新反例。3254 项 unittest 通过、2 项跳过，另 241 项 pytest 通过。PR [#153](https://github.com/HyxiaoGe/fusion/pull/153) 必需检查通过并合并为 `f20120452cbb01144b1ba5ad3e454330489a1978`；dev 工作流 `36217146224` 成功，API/UI 台账与运行镜像核对一致。同句真实 API run `9412c969b5844686a3ee00a5d0b20d5e` 的模型候选再次因天气疑问措辞被误拦，最终退回多日兜底。第七阶段改由模型负责天气条件语义，页面仍待验。见[阶段六报告](reports/backend/2026-09-26-semantic-regex-migration-stage6.md)。

## 2026-09-26 语义正则迁移第七阶段（已部署，范围外日期仍有缺口）

- 连续三次真实天气候选被条件词面规则误拦后，天气描述、建议和疑问语义转交已有结果模型，代码仍检查行政区、明确日期、温度、风向风力和未返回的数值指标。三条真实候选的本地重放均通过；独立复审找到并修复天气词误拦与预警、积水、降水数值漏检。最终版本 3254 项 unittest 通过、2 项跳过，另 241 项 pytest 通过。PR [#154](https://github.com/HyxiaoGe/fusion/pull/154) 合并为 `0d469ace`，master CI/dev 工作流成功，API/UI 台账与运行镜像相符。同句真实天气 run `8640a8181cf54afd8f38677f168d28f7` 直接回答上午降雨无法确认；范围外日期 run `96b827f07d3b486d85d6419787f4dc3c` 的正确候选又被覆盖句误拦，并因历史平均气温内容再次触发事实校验，最终退回四日兜底。第八阶段继续修复，页面待验。见[阶段七报告](reports/backend/2026-09-26-semantic-regex-migration-stage7.md)。

## 2026-09-26 语义正则迁移第八阶段（已部署，范围外日期兜底仍有缺口）

- 第七阶段范围外日期真实候选的自然限制说明被覆盖句泛化正则误拦；移除泛化拒绝，仍核对可解析的具体日期主张是否与返回范围一致。模型提示词聚焦目标日无预报时的简短限制说明，排除无关历史气候统计。原始候选在本地新代码只剩历史平均气温事实拦截；3254 项 unittest 通过、2 项跳过，另 241 项 pytest 通过。PR [#155](https://github.com/HyxiaoGe/fusion/pull/155) 合并为 `e44182e7`，master CI `36219563526` 和 dev 发布 `36219563914` 成功；API/UI 运行身份与镜像台账一致。真实范围外日期 run `6b2975641cec41a8a467f0266d42df58` 的模型候选仍包含历史平均气温，产品事实校验拒绝后退回无关的四日预报。第九阶段修复，页面待验。见[阶段八报告](reports/backend/2026-09-26-semantic-regex-migration-stage8.md)。

## 2026-09-26 语义正则迁移第九阶段（本地补修待发布）

- 第八阶段的真实模型候选未通过产品事实校验，既有兜底未回答范围外目标日期。天气工具现可接收模型判定的单个明确 `requested_date`，天气结果块随本轮保存该日期；失败兜底仅比较结构化目标日期与返回日期集合。曾从用户原文提取日期的初稿因否定意图反例撤回。旧结果、未传日期和多产品块保留原兜底。真实结果快照注入目标日期后的本地重放通过事实校验；完整 unittest 3256 项通过、2 项跳过，额外 CI pytest 241 项通过；独立复审未发现可达 P0/P1。PR/CI、dev API 与页面待验。见[阶段九报告](reports/backend/2026-09-26-semantic-regex-migration-stage9.md)。
