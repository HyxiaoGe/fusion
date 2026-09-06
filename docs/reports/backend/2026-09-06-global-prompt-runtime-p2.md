# Issue #34 P2 验证记录

日期按 Asia/Shanghai：2026-09-06。代码基线 `3c737caf41399b7868bb0e8ec24da2a2a3d6249b`，分支 `codex/issue-34-p2-run-snapshot`。范围与后续分 PR 计划见 [实施计划](../../implementation-plans/2026-09-06-global-prompt-runtime-p2-p5.md)，契约见 [全局 Prompt Runtime 规格](../../specs/backend/2026-09-05-global-prompt-runtime.md)。

## 行为与证据

| 契约 | 验证方式与结果 |
|---|---|
| A Run 冻结后激活 B 不漂移 | 完整包、正文、变量和身份为不可变值；变异输入 payload、并发 task 和 to_thread、异常退出均覆盖。冻结后把 active 读取替换为报错仍可完成所有注册 getter。 |
| 损坏单项拒绝整包 | 11 项 catalog 中一个 checksum 损坏使全部模板使用 code_default；摘要对输入顺序稳定、对 UTF-8 字节变化敏感。P0 未 attested 且正文混合时拒绝伪造单来源。 |
| 分类前有原子身份 | 真实 SQLite 会话事务：分类器读到已提交的 Run 与身份；身份事务失败时分类和主模型均零调用，SSE 输出安全错误终态。 |
| 新 attempt 与取消 | 同 Run 重入拒绝；配置补齐不得更换身份或重置终态；retry/regenerate/continue 分配新 Run 与 attempt_index，保留 previous_run_id 并重新冻结；分类取消令 deadline gate 失效并写 interrupted。 |
| 初始与后续模型输入 | continuation 改传 section id；未知身份拒绝；切包后 continuation/limit summary 仍使用冻结模板。初始快照含知识库及语言系统段落，逐轮动态约束保留各自实际指纹。 |
| 正文允许降级，身份不可缺失 | 原正文表写入失败仍继续生成并报告 degraded；Trajectory 用户读取在正文不可用时返回最小身份，不泄漏配置内额外正文。 |
| 独立辅助调用 | 主 Run A 内启动辅助调用可冻结 B；辅助 completion metadata 含自己的 source_kind/effective_revision，退出后恢复主 Run A。 |

新增核心测试先观察缺少 snapshot factory、身份先于分类缺失、continuation 直接传正文、API 身份缺失、辅助调用沿用旧来源等失败，再实现对应行为。全量首轮的 21 处失败归因于旧 ORM mock 未声明 run_config、跨模块 harness 未接入配置补齐事务和 metadata 的新增字段；补齐测试接缝后原工具行为、事件顺序、盲测期望继续通过。续写 fixture 使用既有合法前态 limit_reached，未放宽运行契约。

## 已运行检查

从本 worktree 的 backend 目录运行，只借用 `/Users/sean/code/fusion/fusion-api/.venv/bin/python` 的完整依赖环境；未修改该旧仓或安装依赖，未启动本地 Fusion 服务。

- 目标回归：`173 passed, 34 subtests passed`；全量失败接缝定向复测 `67 passed, 3 subtests passed`。
- 最终后端全量 `DATABASE_URL='sqlite:///:memory:' <python> -m pytest test/ -q`：`3942 passed, 2 skipped, 9 warnings, 4353 subtests passed`，52.07 秒。
- `<python> -m ruff check app test`、本次 Python 改动的 `ruff format --check`、架构检查、`git diff --check` 均通过。架构检查保留 4 项既有 API 测试文件覆盖警告；pytest 警告为既有依赖弃用提示。
- 本阶段没有新数据库迁移、catalog 扩容、Prompt 英文化、引擎切换、PromptHub 写入或 TTL 调整；P3–P5 按独立 PR 继续。

## 交付层级

本文记录本地代码和自动化证据。独立当前 HEAD 复审、远端 PR/CI、合并、dev 部署、真实模型和已有登录 Chrome 验收须分别追加链接与版本证据，不能由这里的测试结果替代。
