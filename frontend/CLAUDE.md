# Frontend CLAUDE.md

适用于 `frontend/` 的 Next.js / React / Electron 应用。聊天状态按 `SSE → Redux → 渲染 → Dexie/刷新恢复` 核对；架构与编码约定见 [docs/ARCHITECTURE_RULES.md](docs/ARCHITECTURE_RULES.md) 和 [docs/CODING_CONVENTIONS.md](docs/CODING_CONVENTIONS.md)。

<!-- guidance-contract:start -->
## 受控协作约定

- 所有回复、代码注释和 Git 提交信息使用中文；提交格式为 `<type>: <中文描述>`，并保留项目要求的 `Co-Authored-By`。
- 改动范围以 `frontend/` 为应用根；跨到 `backend/` 或根共享文件时，同时读取根导航和后端约定。
- 遇到 bug、状态异常、CI 失败或用户可见回归时先定位根因；行为变更严格先写可失败的测试，再做最小实现。
- AI 协作者不默认启动服务；不得自行启动 Next.js、Electron、本地 Docker 或其他 Fusion 服务，只有用户明确要求时才可启动。
- 按改动运行测试/构建：优先运行目标 Vitest；涉及路由、跨组件协议或产物时运行生产构建及相应检查。
- 用户可见或登录态链路只能复用既有 Chrome 标签；没有已打开且匹配的登录标签时，明确记录验收缺口，不新开浏览器目标。
- 部署/回滚需明确确认；push、PR、合并、外部平台修改和发布是不同授权边界，任何一种授权都不得自动扩展到另一种。
- 用户询问“下一步”时，从仓库根读取 `docs/EXECUTION_LEDGER.md`，执行 `git log --oneline -40`，搜索 `docs/implementation-plans`、`docs/specs`、存在时的 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`、受影响应用文档与源码；台账与当前树或历史冲突时以当前证据为准并指出待更新项。
- 代码审查只提交当前改动引入且具有可达正确性、安全、权限、数据、兼容性或发布后果的 P0/P1；证据不足或仅属 P2/P3 加固时不阻塞。
<!-- guidance-contract:end -->
