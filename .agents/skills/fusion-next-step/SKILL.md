---
name: fusion-next-step
description: Use when the user asks Fusion “下一步”, “接下来做什么”, “还有什么优化”, “还能怎么加强”, roadmap direction, or whether to continue product/infrastructure work.
---

# Fusion 下一步建议

这个 skill 用当前 monorepo 的台账、文档、源码与 Git 历史防止重复建议。不要依赖 Codex memory 或印象推导完成状态。

## 必须步骤

1. 从任意工作目录取得仓库根：`REPO_ROOT=$(git rev-parse --show-toplevel)`；先读 `$REPO_ROOT/docs/EXECUTION_LEDGER.md`。
2. 在仓库根运行并阅读 `git log --oneline -40`。问题只涉及单个应用时，额外运行 `git log --oneline -40 -- backend` 或 `git log --oneline -40 -- frontend`。
3. 用 `rg` 搜索用户关键词，至少覆盖：
   - `$REPO_ROOT/docs/implementation-plans`
   - `$REPO_ROOT/docs/specs`
   - `$REPO_ROOT/backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`（存在时）
   - 受影响应用的文档与源码；跨应用问题同时扫描 `backend` 与 `frontend`
4. 台账与当前 tree 或 Git 历史矛盾时，以当前 tree 与历史为准，明确指出台账待更新；不得用 memory 填补完成状态。
5. 只输出本次证据支持的内容，固定为“已完成事实 / 不应重复建议 / 当前可考虑 / 我的建议”四段。

## 禁止事项

- 禁止把执行台账中已经完成的方向包装成新建议。
- 禁止在没有检查 `git log --oneline -40` 与发现入口时建议历史方向。
- 禁止为了显得有计划而硬凑下一步；没有高置信方向时直接说明当前没有建议。

## 输出格式

```markdown
我先查了执行记录。结论：

- 已完成事实：...
- 不应重复建议：...
- 当前可考虑：...

我的建议：...
```
