# Fusion 执行台账

> `docs/EXECUTION_LEDGER.md` 是 Fusion monorepo 唯一的执行事实源。回答“下一步”“还能怎么优化”“接下来做什么”之前，必须从仓库根读取本文件，运行 `git log --oneline -40`，再核对 `docs/implementation-plans`、`docs/specs`、存在时的 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md` 以及受影响应用的文档与源码。

## 使用规则

- 不把 Codex memory 当执行记录；memory 只能作为偏好和约束提示。
- 每次重大功能、核心链路、发布门禁或真实回归完成后，在本文件补一条记录。
- 如果方向已经在“已完成基线”或“不要重复建议”中出现，不得作为下一步建议重新提出，除非用户明确要求返工或扩展。
- 如果当前文件和 `git log` 冲突，以当前 worktree 和 git 历史为准，并更新本文件。

## 已完成基线

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

1. 读本文件。
2. 从仓库根运行并阅读 `git log --oneline -40`；只涉及单个应用时再按 `-- backend` 或 `-- frontend` 过滤。
3. 用 `rg` 搜索相关关键词，至少覆盖 `docs/implementation-plans`、`docs/specs`、存在时的 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md` 以及受影响应用文档与源码。
4. 先列“已完成事实”，再列“不能重复建议”，最后才给新的建议。
5. 如果没有高置信下一步，直接说“当前不建议继续开基础设施优化坑”，不要硬凑方向。

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
