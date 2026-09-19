---
name: fusion-next-step
description: 根据当前代码和执行记录评估 Fusion 路线、历史完成状态与后续工作，避免重复建设。
---

# Fusion 路线与状态判断

用 `git rev-parse --show-toplevel` 定位当前 monorepo。从 `docs/EXECUTION_LEDGER.md` 查相关主题，再核对当前树和相关 `git log`；历史台账和 memory 不能替代当前证据。

- 实施设计按需查 `docs/implementation-plans` 和 `docs/specs`，模型验收查 `backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md`，其他问题查受影响应用的文档与源码。
- 按主题、路径和近期提交缩小查询；只有信息不足才扩展历史，不固定要求每次扫描同一批文档或 40 个提交。
- 已经完成的方向不能重新包装成新建议；用户明确返工或扩展时指出与已完成部分的区别。
- 台账与代码、Git、CI 或运行事实冲突时说明差异。只读咨询只指出待更新项，不自行改写历史或外部状态。
- 先回答用户当前决策，再给关键事实、价值与取舍；没有充分依据就说明尚不能推荐，不填满固定模板。
