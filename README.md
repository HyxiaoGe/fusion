# Fusion

Fusion 是由两个独立应用组成的 monorepo：

- `backend/`：FastAPI 后端；开发入口见 [backend/README.md](backend/README.md)，协作约定见 [backend/AGENTS.md](backend/AGENTS.md)。
- `frontend/`：Next.js / Electron 前端；开发入口见 [frontend/README.md](frontend/README.md)，协作约定见 [frontend/AGENTS.md](frontend/AGENTS.md)。

开发、测试与构建命令分别以各应用 README 和 AGENTS 为准。跨应用执行事实统一记录在 [docs/EXECUTION_LEDGER.md](docs/EXECUTION_LEDGER.md)，当前实施计划位于 `docs/implementation-plans`，设计规格位于 `docs/specs`，模型验收手册位于 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`；查询近期变更时在仓库根运行 `git log --oneline -40`。

当前开发已迁移至本仓库。原 `HyxiaoGe/fusion-api` 与 `HyxiaoGe/fusion-ui` 仍在外部退役流程中；待其 workflow、runner、README 与归档等外部步骤完成后，旧仓才仅保留历史追溯用途。不得由本 README 推断外部退役已经完成。
