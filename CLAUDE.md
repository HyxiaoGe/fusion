# Fusion Monorepo Claude 导航

本文件只负责把协作者路由到权威约定，不重复应用实施规则。

- 修改 `backend/`：先读 [backend/CLAUDE.md](backend/CLAUDE.md)；同时遵循 [backend/AGENTS.md](backend/AGENTS.md)。
- 修改 `frontend/`：先读 [frontend/CLAUDE.md](frontend/CLAUDE.md)；同时遵循 [frontend/AGENTS.md](frontend/AGENTS.md)。
- 跨应用改动：同时读取两组应用约定，以更严格的边界为准。
- 仓库执行事实源是 [docs/EXECUTION_LEDGER.md](docs/EXECUTION_LEDGER.md)；实施计划在 `docs/implementation-plans`，规格在 `docs/specs`，模型验收手册在 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`。
- 用户询问 Fusion 下一步时使用 [.agents/skills/fusion-next-step/SKILL.md](.agents/skills/fusion-next-step/SKILL.md)，并在仓库根执行 `git log --oneline -40` 后核对当前树。
