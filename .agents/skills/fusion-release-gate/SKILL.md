---
name: fusion-release-gate
description: 完成已授权的 Fusion 分支推送、PR、CI 或 dev 发布，并核验对应交付状态。
---

# Fusion Git 与发布交付

使用当前 `HyxiaoGe/fusion` worktree 的根 AGENTS.md 和实际 workflow。本 skill 的权威版本随仓库维护，发布规则不由旧双仓目录或历史 memory 推导。

用户指定的工作树优先。当前目录不是 Git 仓库时，从本 skill 文件的真实路径定位所属仓库并核对 remote，再进行发布前检查。

## 先核对实际影响

- `backend/`、`frontend/` 属于同一仓库。根 `.github/workflows/pr-ci.yml` 验证 PR/master；`.github/workflows/deploy-dev.yml` 在 master push 或手动触发时按影响选择应用。
- 推送特性分支不等于 CI 已运行；创建/更新面向 master 的 PR 后核对实际 run。PR 检查不等于镜像发布或部署。
- 合入 master 会触发 dev 发布，确认已有授权包含这个后果。仅 push/PR 授权不含合并与部署，独立 production 目标不能自行推断。
- 合并或部署前先准备可审查的 diff、测试结果和目标版本；只有缺少必要授权时才在该动作前请求确认。先前明确授权持续有效。

## 执行与核验

- 检查当前分支、status 和基线差异，只暂存本任务文件，按根 AGENTS.md 提交。记录 SHA、PR 和相关 run。
- 跟踪当前提交对应的 required checks。失败时检查具体 job/step，修复本次引入的问题并重新验证；用户已明确授权修复全部 CI 阻塞时按该范围处理。不要以关闭检查或修改线上源码换取通过。
- 等待 CI 时可做独立工作，减少重复查询，直到授权要求的结果可核对或遇到具体阻塞。
- 部署完成后逐应用核对 `.github/workflows/_deploy-api.yml`、`_deploy-ui.yml` 对应结果、运行 digest/image ID 和发布台账。后端 `dev-verify` 提供只读操作入口；某应用 skipped 不能写成它已部署。
- 产品路径使用仓库 `fusion-acceptance`，真实环境条件缺失时记录缺口。回滚是独立的环境变更，已有授权不含回滚时先准备具体方案再确认。

最终只报告本次需要的层级：已推送、PR/CI、合并、实际部署身份、目标行为。需要真实验收的任务不得停在 CI 绿灯；用户只授权 PR 的任务完成到 PR/CI 即可。
