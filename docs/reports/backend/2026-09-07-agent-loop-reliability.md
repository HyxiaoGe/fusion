# Agent loop 可靠终止与输出归因验证

## 交付范围

基于 `ba6e108a080fb9c0ebe809be1d3585c1a65c2b86`，在 `codex/agent-loop-reliability` 隔离分支开发。保留自实现循环、本地 prompt 和现有业务策略，不引入框架、观测服务或数据库迁移。

- `6c5cd2c5`：服务端 checkpoint/取消/失败保留真实工具结果；并行工具异常后等待清理；预算触顶交付现有产品事实与未完成说明。
- `8009f090`、`c230a823`：修复独立审查发现的两种重复取消时序，从首次聚合等待开始隔离父取消，等待工具异步清理并消费所有 future 异常。
- `d9ad4387`：模型正文处置通过既有 terminal 事件、账本、详情 API 与 trajectory 界面贯通；Linux/Windows 测试入口纳入新增回归。

归因区分模型原文采用、服务端改写或替换、候选未采用，以及已展示后撤回；保留原候选，旧记录显示未知。元数据只有固定分类和有界输出块 ID，无全文复制。工具回合在执行工具前结束模型事件；无模型收尾不伪造或借用上一轮模型身份。

## 自动化证据

执行时区均为 `Asia/Shanghai`，只使用 SQLite、受控异步工具、mock 模型流与组件测试，没有启动应用服务。

| 检查 | 结果 |
| --- | --- |
| 主代理跨任务后端目标集合 | 557 passed，194 subtests passed |
| 主代理前端轨迹归一化、投影、详情与 Tab 五个文件 | 192 passed |
| 根 `.github/scripts` 契约测试 | 67 tests，OK |
| 改动 Python 文件 Ruff / format check | 19 个文件通过 |
| 改动 TypeScript/TSX 文件 ESLint | 通过 |
| 后端架构检查 | 通过；保留 4 条既有 API 测试文件命名缺失警告 |
| 前端 production build | 通过；保留既有 caniuse-lite 数据过旧提示 |
| 完整 TypeScript 检查 | 未通过；与改动前 25 处既有错误逐字一致，无新增错误 |
| Linux 测试脚本 `bash -n`、`git diff --check` | 通过 |

构建配置原本跳过类型检查和 lint，不能以构建成功替代独立静态检查。后端目标集合的 8 条警告均来自既有 Pydantic Config 和 SQLAlchemy declarative_base 弃用。

后端目标集合覆盖本轮四个新增测试文件，以及 agent round、lifecycle、observability、driver/outcome/wiring、run completion、limit summary/fact guard、persistence、executor、chat stop、trajectory API/授权查询/安全投影/账本与详情录入。前端覆盖 `normalizeTrajectoryEvent`、`TrajectoryCellProjection`、`TrajectoryNodeDetailPanel`、`TrajectoryTabView` 和 stale inspect。

复核日志保存在本机 `/private/tmp/fusion-agent-loop-{pytest,vitest,tsc,build}-final.log`。类型基线为 `/private/tmp/fusion-agent-loop-tsc-baseline.log`，对比退出码为 0；类型检查本身退出码为 2。

## 关键回归与审查

- 持久化测试通过新的 SQLite Session 读取，确认取消/失败后的 search 卡片仍在；客户端伪造 search 块仍被拒绝。
- 真实 executor 的受控子任务覆盖所有权丢失、普通取消、清理期间再次取消与正常并发。独立审查发现第二次 cancel 会打断慢工具清理，快工具先退出的时序修复后，最终审查又复现单个慢工具在首次 gather 等待期间遭遇两次父取消的缺口；`c230a823` 从首次等待开始使用 shield，新增回归确认清理完成后才抛第一次取消，且无未读取 future 异常。
- max_steps、max_tool_calls、timeout 配合未完成计划，检查实际正文、未完成说明、最终 limit_reached，且无新增模型或工具调用。
- 正文归因覆盖 thinking 先输出、部分输出后失败/取消、延迟采用、产品编辑/确定性替换、知识/研究门禁、计划抑制、工具正文撤回、总结与纯服务端终局。
- SQLite 详情查询验证会话、Run、round 与 owner 隔离；前端测试覆盖实时 terminal 优先于旧详情缓存、旧历史未知、中英文及有界字段白名单。
- Task 1 独立规格与质量复审通过，重复取消缺口已关闭。Task 2 独立规格与质量审查通过；最终全分支审查及 `c230a823` 定向复审通过，唯一 P1 已关闭，无剩余可达 P0/P1。独立复现确认 release 前批次未退出、release 后清理完成并保留第一次取消，GC 后未处理异常数为 0。

## 验收边界

本报告只证明本地实现与隔离自动化验证。没有 push、PR、远端 CI、合并、部署，也没有真实登录态浏览器、真实模型或外部工具验收。取消清理不承诺撤销外部系统已经执行的动作。

本地分支和工作树保留供审阅；发布及真实环境验收需要另外执行，不能从上述测试结果推断已经完成。
