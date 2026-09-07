# Agent loop 可靠终止与输出归因实施计划

> **执行技能：** 使用 superpowers:subagent-driven-development 逐项执行，主代理保留审查、交叉验证和交付；用户已授权开发，不重复询问执行方式。

**目标：** 停止与触顶时正确保存和收尾，并让轨迹说明模型候选如何被处理。

**架构：** 在现有 persistence、tool executor、driver/outcome 的实际路径修复边界。归因通过现有事件账本连接输出决策、详情 API 和 UI，保持旧事件/历史兼容。

**技术栈：** Python/FastAPI、SQLAlchemy、asyncio、pytest；TypeScript/React、Vitest。

**规格：** `docs/specs/backend/2026-09-07-agent-loop-reliability.md`。

## 全局约束

- 所有回复、代码注释和 Git 提交信息使用中文。
- 保留本地 prompt、自实现循环、现有业务策略与授权/代际保护。
- 不引入框架、数据库迁移、全量输入快照或新的观测服务。
- 不启动本地服务，不推送、创建 PR、合并或部署。
- 测试使用隔离依赖，不调用真实模型或业务工具；运行时区为 Asia/Shanghai。

### Task 1: 可靠终止

**文件：** `backend/app/services/stream/persistence.py`、`tool_round.py`、`tool_executor.py`、`agent_loop_driver.py`、`agent_loop_round_outcome.py`、`agent_loop_run_completion.py` 及其目标测试。若服务端与客户端共享 partial helper，按可信调用边界显式区分，扫描所有调用点。

**接口：** 保留现有持久化返回语义、task ownership 异常与循环 outcome；终局收尾明确不要求下一轮，输出归因任务随后接入实际提交答案的边界。

- [x] 写失败测试：真实 SQLite 新 Session 校验 checkpoint 与取消/失败后 search 仍存在，客户端伪造 search 仍被拒绝；同代正常完成作对照。
- [x] 写失败测试：一个真实 executor 子任务抛 StreamOwnershipLostError，另一个受控工具必须先完成取消清理，批次再抛原异常。增加正常并发和普通取消对照。
- [x] 写失败测试：合法计划中地点完成、天气待办，分别触发 max_steps/max_tool_calls/timeout；收集真实输出正文，断言包含已知地点及未完成提示，没有新增模型/工具调用或 plan_execution_repair。
- [x] 运行新增测试，确认失败来自目标行为；复现参考 `/private/tmp/fusion_loop_partial_sqlite_repro.py`、`/private/tmp/fusion_loop_gather_repro.py`、`/private/tmp/fusion-loop-review-y8m85vjg/review/test_terminal_output.py`，正式测试必须自包含。
- [x] 实现最小修复：可信服务器持久化保留新增结果；并发异常清理后重抛；触顶走不再请求后续执行的终局路径。
- [x] 运行 persistence、tool executor/tool round、driver/outcome、run completion 目标测试与改动文件 Ruff，检查 diff。
- [x] 精确暂存本任务文件并中文本地提交；写报告与提交 SHA，接受独立审查。

测试解释器：`/Users/sean/code/fusion/fusion-api/.venv/bin/python`。从 `backend/` 执行 `TZ=Asia/Shanghai LITELLM_LOCAL_MODEL_COST_MAP=True <解释器> -m pytest <目标测试文件> -q`。

### Task 2: 输出归因闭环

**文件：** 后端 `stream/agent_round.py`、`stream/llm_round_lifecycle.py`、`stream/agent_loop_round_outcome.py`、相关收尾路径，`services/agent/events.py`、`emitter.py`、`trajectory_payload.py`，`schemas/trajectory.py`、`services/trajectory_query_service.py` 及必要的仓库查询；前端现有 trajectory 类型、事件归一化/投影/详情模型、`TrajectoryNodeDetailPanel.tsx` 与 i18n；对应目标测试。

**接口：** 使用现有 run_id、llm_round_id、step_id、文本块 ID。新增有界归因元数据，emitted/suppressed/replaced 与 model/server/none 的含义按规格；新增字段可缺省。不得依赖 Task 1 终局生成不存在的模型 round。

- [x] 写失败测试：生产 round/outcome 输出路径中普通模型正文、延迟采用、产品与知识库替换、计划抑制分别产生正确归因；取消/失败后已有可见内容不被错误标为未输出。
- [x] 写失败测试：真实事件白名单和历史详情查询保留归因、隔离跨会话/跨轮数据；旧历史缺失时为未知。事件不包含候选/答案全文。
- [x] 写前端失败测试：现有详情面板显示“已输出 / 未采用 / 已改写或替换”的状态、来源及原因；已撤回正文与从未发布区分；模型候选仍可查看；旧响应不伪造采用状态。
- [x] 在实际输出与策略决策处发出事件，扩展现有安全投影和详情 DTO；非模型收尾不关联前一轮，不新增数据库表/列。新增观测 sink 错误保留既有运行异常和降级方式。
- [x] 贯通前端 live/history 接收和详情展示，使用现有 API、Redux/查询链路及中英文资源。
- [x] 运行目标 pytest/Vitest/Ruff/ESLint，执行前端类型检查和生产构建；若存在基线问题，使用同 SHA 干净树核对，不能静默忽略。
- [x] 把本轮新增函数式 pytest 回归接入 `backend/.github/scripts/linux-build-and-test.sh` 与 `windows-build-and-test.ps1` 的既有显式 pytest 入口；保留原 unittest/pytest 检查和超时，确认不会只在本地运行而被 CI 漏收集。
- [x] 精确暂存本任务文件并中文本地提交；写报告与提交 SHA，接受独立审查。

### Task 3: 综合验证与交付

- [x] 主代理亲自读取最终差异并重跑跨任务目标集合，检查已有成功路径与新增边界。
- [x] 独立审查整个分支的规格符合性与可达正确性问题，修复后复核。
- [x] 更新规格验收记录与 `docs/EXECUTION_LEDGER.md`，准确标明本地代码证据、未推送/部署及真实环境未验收。
- [x] 检查 `git diff --check`、工作区与提交范围，保留分支/worktree 供用户审阅。
