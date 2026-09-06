# P3b：持久回滚 hold 与代码回滚边界

日期：2026-09-06，时区 Asia/Shanghai。开发基线：P3a `90962a5c05368d6fbe038c95de6de5bc59da310a`。本阶段依赖 P3a 完整 v2；不接受 v1 作为 hold 目标，不修改历史 Run。

## 数据与管理操作

- 新增 `prompt_bundle_hold_states`：复合主键 `(project_slug, catalog)`，记录 `following / held`、目标 revision、generation 和更新时间。逻辑 catalog 为 `fusion`，不把物理存储键当作用域身份。
- 新增 `prompt_bundle_hold_transitions`：记录 entered/released、目标 revision、generation、登录操作者、原因与 Asia/Shanghai 时间。每次状态转换和审计在同一事务内落库，数据库禁止 UPDATE/DELETE；不随用户删除级联清除。
- `GET /api/admin/prompt-bundle/hold` 直读当前状态及 active 身份；`POST /api/admin/prompt-bundle/hold` 激活指定完整 v2 并进入 held；`POST /api/admin/prompt-bundle/hold/release` 解除该目标的 hold。两个写请求都要求 `target_revision` 和非空 `reason`；actor 固定来自登录管理员，额外请求字段被拒绝。
- 相同目标重复 enter、已 following 时重复 release 不重复写事件；held 期间切换到不同 hold 目标或使用陈旧目标解除返回冲突。通用 runtime config 写入口对 `prompt_bundle` 仍只读。

## 原子性与同步

进入 hold 先取得原有 Prompt advisory transaction lock，校验当前 catalog/来源/本地 checksum/物理行 revision 与 P0 原字节门禁，再激活并显式 flush；随后更新状态、追加事件，只提交一次。任何一步失败均回滚。release 仅切换 following，下次正常同步才激活届时完整 published revision。

所有同步激活路径在锁内直接查询 hold 表，没有进程缓存参与安全决定。held 时继续抓取、校验、记录差异和诊断；通过门禁的新候选只存 inactive。远端超时或 5xx 不阻塞本地 enter/release。既有聊天读取缓存仍保留原 TTL，数据库 active 切换与所有 worker 新 Run 的收敛必须分别验收；P4 的调度/TTL 调整不提前实施。

PostgreSQL 保护触发器使用同一锁，阻止 held 目标被停用、删除、改写或被另一 active revision 替换，因此已回滚的旧 P3a 写入者也不能绕过 hold。触发器显式声明 VOLATILE，应用锁入口、触发器及 preflight 均要求 READ COMMITTED，拒绝可能早于持锁时点的陈旧事务快照；该选择依据 [PostgreSQL 的函数快照说明](https://www.postgresql.org/docs/17/xfunc-volatility.html)。SQLite 分支用于自动化事务验证，不能替代生产 PostgreSQL 的并发证明。

## 未来另行授权后的发布与回滚

1. 先应用新增迁移 `e4b6c9d2a701`，保留 P3a 已有 v2 和旧行。新 hold 代码缺少该 schema 时，部署 preflight 拒绝启动。
2. 当前 master 发布脚本把 preflight 代码注入所选不可变目标镜像，使用最终数据库/Prompt 配置运行真实完整包校验、P0 门禁和 freeze；不依赖旧镜像自带新脚本。
3. hold schema 启用后，所有受该发布器管理的正向部署和代码回滚目标均至少支持完整 v2，即使此刻是 following 也不允许回退 P2/v1；这避免 preflight 后才进入 held 的竞态。历史旧 workflow 的重跑不属于受保护入口。
4. 失败回滚先核对旧 ref/ID，再进行同样的目标检查；恢复后再次调用运行容器的真实 freeze。支持 v2 的 P3a 可在数据库保护下读取 held 版本；不 downgrade hold schema 或删除触发器。
5. held 验收以持久 target 与真实完整冻结为依据；远端同步状态独立记录，远端故障不能迫使安全 held 回滚失败。following 仍执行正常远端完整包同步验收。

migration downgrade 明确拒绝执行，避免代码回滚顺带丢失 hold 与历史审计。旧 v1 停用仍遵循 P3a 的 worker/回滚锚点前置条件，不能借本阶段自动删除或重写历史数据。

## 证据边界

测试包含正常转换、重复和冲突、提交失败、完整性及 P0 负向、跨项目隔离、独立 Python worker 重启和陈旧 bundle 缓存、SQL 直接改写/删除拒绝、旧 P3a 实际同步器的新旧 revision 激活路径、目标镜像最低能力、缺失迁移、真实脚本拼接与远端超时。

历史 P3a 同步器逐字保存在测试夹具并校验 SHA-256，不随新实现调整。独立工作树代码复审未发现可达 P0/P1，另一独立契约审查提出的回滚能力下限、数据库保护和真实冻结验证均已落实。正式提交后再次进行当前 HEAD 复审。

最终本地 pytest 为 `4031 passed, 2 skipped, 4381 subtests passed`，unittest 为 `3044 tests, OK (skipped=2)`，根目录部署/CI 契约为 `67 tests, OK`。Ruff、架构检查、改动文件格式、shell 静态检查与 diff 检查通过。上述结果是本地自动化证据，不等同于 PR CI 或部署验收。

本次没有启动本地 Fusion/Docker/数据库服务，没有访问 dev、写 PromptHub、调整 GitHub 变量或执行迁移。真实 PostgreSQL 并发锁等待、线上多 worker 收敛、授权发布后的新 Run 验收仍属环境门禁，不能由 SQLite 或 mock 结果代替。
