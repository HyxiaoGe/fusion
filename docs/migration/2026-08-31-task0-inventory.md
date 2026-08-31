# Task 0 基建与恢复点清单

盘点时间：2026-08-31 21:16 CST（Asia/Shanghai）

本清单只记录新仓基建、可恢复状态与外部绑定，不执行代码搬迁或 dev 发布。secret 只记录名称和状态，不记录值。

## 仓库基线

| 仓库 | 当前主工作区 HEAD | `origin/master` | 状态 |
|---|---|---|---|
| `fusion-api` | `c5e2b73e6a735e0b2815aaa12c61fc4c534fc78b` | `e76af13257b2a8f660f1723331cdb9142513bc58` | 主工作区有 9 个未跟踪文档；另有 3 个 worktree 存在未提交状态 |
| `fusion-ui` | `77b7fc22a71578b0e1c915fa13827d4ae7e2c063` | `77b7fc22a71578b0e1c915fa13827d4ae7e2c063` | 全部已登记 worktree 干净 |

API 的 4 个未提交现场如下，均保持原位且未改动：

- `/Users/sean/code/fusion/fusion-api`：9 个未跟踪的 plan/spec 文档。
- `/Users/sean/code/.worktrees/fusion/runtime-master/fusion-api`：`.DS_Store` 为删除状态。
- `/Users/sean/code/fusion/.worktrees/run-capability-router-api`：3 个未跟踪的路由审查文档。
- `/Users/sean/code/fusion/.worktrees/trajectory-ledger-p0-api`：`docs/TRAJECTORY_DESIGN.md` 已修改。

## 恢复点

恢复目录不在任何仓库 checkout 内：

`/Users/sean/code/fusion-workspace/recovery/2026-08-31-monorepo-task0`

| 归档 | SHA-256 | 验证 |
|---|---|---|
| `fusion-api.bundle` | `94c80d574285ee8794c7ed423d34b3ed2bf305d1ef973ca79a2c23cca6db9862` | `git bundle verify`、完整 refs 比对、全部 worktree HEAD 对象检查通过 |
| `fusion-ui.bundle` | `3cc519f6bc013f2dbcfbd83a83ed865563bbb75433a19a5f4955a00f87750307` | `git bundle verify`、完整 refs 比对、全部 worktree HEAD 对象检查通过 |
| API 主工作区未跟踪归档 | `fb525b0e905ddacb91615c646f0fa70214434d6ceb7645881c6536e985584387` | 解包后逐文件 SHA-256 与 `git status --porcelain` 比对通过 |
| API 路由 worktree 未跟踪归档 | `047931fdf3e7b5edc86f403430a013541ea4c54a67da1d21525cc21afd2f6efd` | 解包后逐文件 SHA-256 与 `git status --porcelain` 比对通过 |

总清单 `SHA256SUMS` 共校验 219 个文件。4 个 API 未提交现场均已从 bundle + patch + 未跟踪归档还原，状态差异文件均为 0 字节；UI 无未提交现场。

## GitHub 配置

旧仓共同配置：

- 默认分支为 `master`，仓库均为 public。
- `dev` Environment 使用 custom branch policy，唯一允许分支为 `master`。
- `master` 禁止 force push 和删除，管理员也受保护；PR review 规则启用且 stale review 会被 dismiss。
- required status check 为 `PR container validation`。新仓 Task 0 不复制该 check，待 Task 1 在新仓成功产生新的恒定 gate 后再设置。
- GitHub Actions 启用，允许全部 Actions，未要求 action SHA pinning。
- 两个旧仓均无 repo deploy key、无 repo webhook。

新仓 bootstrap 后应完成：创建 `dev` Environment 与 `master` policy；复制非 check 类保护；复制可公开读取的 Actions variables。required status check 保持空缺。

## Secrets 与 variables

### Repo secrets

| 来源 | 名称 | 新仓状态 |
|---|---|---|
| API | `LITELLM_MODEL_ADMISSION_WORKER_TOKEN` | 待从原始凭据源重新注入；不得从运行环境反向导出 |

### `dev` Environment secrets

| 来源 | 名称 | 新仓状态 |
|---|---|---|
| API/UI | `ACR_PASSWORD` | 待从原始凭据源重新注入 |
| API/UI | `ACR_USERNAME` | 待从原始凭据源重新注入 |
| API | `AMAP_MCP_API_KEY` | 待从原始凭据源重新注入 |
| API | `DASHSCOPE_API_KEY` | 待从原始凭据源重新注入 |
| API/UI | `FEISHU_INFRA_WEBHOOK` | 待从原始凭据源重新注入 |
| API | `FLYAI_API_KEY` | 待从原始凭据源重新注入 |
| API | `MILVUS_PASSWORD` | 待从原始凭据源重新注入 |
| API | `PROMPTHUB_API_KEY` | 待从原始凭据源重新注入 |
| API/UI | `PUSHGW_BASIC_AUTH` | 待从原始凭据源重新注入 |

同名项在新仓只保留一份。原始值不可得时必须轮换；连接验证留待注入后执行。

公开 variables 按原名称和值迁移：API repo 3 项、API `dev` 10 项、UI repo 3 项。执行结果在 GitHub 配置完成后回填。

## Runner

旧仓 4 个 runner 均保持 online 且不迁移：

| 仓库 | runner | 平台 | 当前标签 |
|---|---|---|---|
| API | `dev-server-fusion-api` | Linux X64 | `self-hosted`, `Linux`, `X64` |
| API | `windows-build-api-01` | Windows X64 | `self-hosted`, `Windows`, `X64` |
| UI | `dev-server-fusion-ui` | Linux X64 | `self-hosted`, `Linux`, `X64` |
| UI | `windows-build-01` | Windows X64 | `self-hosted`, `Windows`, `X64` |

新仓 runner 尚未注册。计划新增 API/UI 各一套 Linux 与 Windows runner，并分别增加 `fusion-api` / `fusion-ui` 标签；`.github/workflows/task0-runner-smoke.yml` 只允许手动触发。公开仓库的自托管 runner 会建立长期代码执行入口，注册与持久服务启用需单独明确授权。

## dev 宿主机状态

- `~/project/fusion/.env`：`0600 heyanxiao:heyanxiao`；`STORAGE_BACKEND=oss`。
- 兼容挂载源 `~/project/fusion/fusion-api/storage/files` 存在，owner/mode 为 `root:root 0755`；本轮不迁移其内容。
- API 与 knowledge worker 使用镜像 tag `e76af13257b2a8f660f1723331cdb9142513bc58`，image ID `sha256:c846d2b57e736b355a7521691044cd9b4999c691f13d98dd324367b88f2a4712`。
- FlyAI adapter 使用同一提交 tag，image ID `sha256:88c9c43eb0c48e3509c04c43235768111b97b419bfa2ff375b07261a73cf2d15`。
- UI 使用 tag `77b7fc22a71578b0e1c915fa13827d4ae7e2c063`，image ID `sha256:9d4db61d30ab0eea39d70fd538c2c95325856b6b42c277495eda0d9c451d42c2`。
- API bind mounts：`~/backups/litellm-governance -> /var/lib/fusion/litellm-governance`、`~/project/fusion/fusion-api/logs -> /app/logs`、`~/project/fusion/fusion-api/storage/files -> /app/storage/files`。
- knowledge worker bind mounts：`~/project/fusion/knowledge-worker-logs -> /app/logs`、`~/project/fusion/fusion-api/storage/files -> /app/storage/files`。

三个 user systemd unit 的 fragment 均为 `0644 heyanxiao:heyanxiao`：

| Unit | WorkingDirectory | 盘点瞬时状态 |
|---|---|---|
| `fusion-litellm-cost-sync.service` | `~/.local/share/fusion/litellm-governance-src-1c5757689e6393eb1e96e115b406d8ed1ddeb0e5` | inactive/dead |
| `fusion-litellm-governance.service` | `~/.local/share/fusion/litellm-governance-current` | inactive/dead |
| `fusion-litellm-model-management.service` | `~/.local/share/fusion/litellm-model-management-current` | activating/start |

前两项为 timer 驱动的一次性 service；Task 2 切换时仍需按 unit/timer 语义逐项验证。

## Vercel / Railway

| 应用 | 配置证据 | GitHub 侧证据 | 当前判断 | Task 归属 |
|---|---|---|---|---|
| API / Railway | `fusion-api/railway.json` | 无 webhook、无 deploy key；近期 deployment 均为 GitHub Actions 的 `dev` | 尚不能证明服务是否活跃、绑定哪个 repo/branch 或是否自动部署 | 待控制台核实；若进入当前 dev 链路则 Task 2，否则 Task 4 |
| UI / Railway | `fusion-ui/railway.json` | 无 webhook、无 deploy key；近期 deployment 均为 GitHub Actions 的 `dev` | 尚不能证明服务是否活跃、绑定哪个 repo/branch 或是否自动部署 | 待控制台核实；若进入当前 dev 链路则 Task 2，否则 Task 4 |
| UI / Vercel | `fusion-ui/vercel.json` | 无 webhook、无 deploy key | 尚不能证明项目是否活跃、绑定哪个 repo/branch 或是否自动部署 | 待控制台核实；若进入当前 dev 链路则 Task 2，否则 Task 4 |

本机没有 `vercel` / `railway` CLI，也没有仓库内 `.vercel/project.json`。在获得已登录控制台证据前，不把配置文件的存在误判为活跃绑定。

## 未闭环项

- 明确授权后注册新仓 4 个 repo-scoped 自托管 runner，启用持久服务，并手动运行 smoke workflow。
- 从原始凭据源重新注入 10 个唯一 secret 名称，逐项记录来源、位置、连通性和轮换状态。
- 在已登录控制台核实 Vercel / Railway 活跃性与 repo/branch/自动部署绑定。
