# Fusion 协作约定

这是 `HyxiaoGe/fusion` monorepo：`backend/` 是 API，`frontend/` 是界面，根 `.github/workflows/` 和 `ops/` 管理共享检查与发布。用 `git rev-parse --show-toplevel`、remote 和当前树确认位置，不从旧目录名或 memory 推断仓库。

## 执行边界

- 回复、代码注释和提交使用中文；时间按 `Asia/Shanghai`。提交标题为 `<type>: <中文描述>`，附 `Co-Authored-By: Codex <noreply@anthropic.com>`。
- “看下 / 分析 / 审查”只调查；“修下 / 开始 / 继续”按已约定范围完成实施与验证。已有授权持续有效，常规定位、修改、目标测试和授权范围内的失败修复无需逐步确认。
- push、PR、合并、部署、回滚和外部平台修改按用户实际授权执行；一次明确授权可以覆盖多个动作。合入 `master` 会触发 dev 发布，合并前把这个后果纳入授权判断。数据删除、权限变更、生产操作和额外付费操作不得从普通修复请求推导。
- 只有缺失信息会改变方向、不可逆后果或权限边界时才暂停依赖它的操作，先完成独立工作。工具审批阻止执行时说明具体动作和原因。
- 保留用户已有改动，只修改和暂存本任务文件；不强推，不用 hard reset、clean 或全仓格式化处理无关内容。
- 本项目使用下列仓库 skills，不依赖 Superpowers 的设计审批或流程编排。

## 按任务读取

| 工作 | 入口 |
|---|---|
| 后端改动 | [backend/AGENTS.md](backend/AGENTS.md) |
| 前端改动 | [frontend/AGENTS.md](frontend/AGENTS.md) |
| 开发、修复与定位 | [fusion-change-loop](.agents/skills/fusion-change-loop/SKILL.md) |
| 已授权的 Git、CI 与发布交付 | [fusion-release-gate](.agents/skills/fusion-release-gate/SKILL.md) |
| 真实环境与页面验收 | [fusion-acceptance](.agents/skills/fusion-acceptance/SKILL.md) |
| 路线建议或历史完成状态 | [fusion-next-step](.agents/skills/fusion-next-step/SKILL.md) |

跨应用或协议改动读取两侧约定，小改动只读取相关入口。架构资料、计划和历史报告按问题需要查找，不要求每次遍历仓库。

## 验证与完成

- 先明确可观察的预期行为。bug 修复应有能暴露原故障的回归证据；纯文档、低风险样式或可逆配置按实际风险验证，不编写复刻实现的测试。
- 运行受影响测试及必要检查；共享状态、认证、流式、持久化和构建链路扩大到相关边界。没有新改动、失败或未解疑点时不反复跑通过的检查。
- 独立子任务能节约时间或减少盲点时使用子代理，小任务直接完成。高风险行为安排独立审查，主代理核对实际差异和证据。
- 不默认启动服务；已明确授权本地运行或真实 dev 验收时，按授权继续，无需重复申请。
- 页面验收默认通过官方 Chrome 扩展连接用户现有登录态，绑定已打开的匹配标签，按[验收 skill](.agents/skills/fusion-acceptance/SKILL.md)操作。使用标签级浏览器接口；只有确需原生界面操作时才使用桌面控制。连接失败不新开浏览器进程、配置目录、标签或内置浏览器替代；继续其他检查并记录页面缺口。用户明确指定其他目标时遵循本次选择，通用 skill 的默认启动流程不能覆盖此规则。
- 完成到本次授权的终点。代码检查、CI、部署版本、真实路径和用户验收分别说明；缺少某层证据不能声称该层通过。最终先说结果，再给关键验证及剩余缺口，不用固定长模板。
- 重大功能或发布验收结果记入[执行台账](docs/EXECUTION_LEDGER.md)，详细证据另存报告。当前代码、Git、CI 和运行证据优先于历史记录；更新台账不得抹掉历史事实。
