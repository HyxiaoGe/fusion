# P3a：v2 完整性与部署前桥接

日期：2026-09-06，时区 Asia/Shanghai。基线 `ed3a789365a46c5724420d2ffec18d6f06040e81`。

本阶段从真实原始 published 材料生成独立 v2，不从旧 v1 推断字段。v2 运行时只读 `prompt_bundle/fusion:v2`，旧 `prompt_bundle/fusion` 保留给桥接期间的旧 worker。普通同步不会覆盖同 revision 行，也不会将 shadow 作为取消 active 的操作。

## 完整性与来源身份

- 原始 variables 的 JSON 结构、字典内容及列表顺序保留在 v2；归一化名字只供变量业务校验。
- `source_revision` 严格遵守 PromptHub canonical：项目 slug、按 slug 排序的 Prompt slug/version/正文 SHA-256/原始 variables。format、engine、发布时间及本地 schema/catalog 不混入来源摘要。
- `local_payload_checksum` 覆盖除自身以外的完整本地封装，包括 schema/catalog、来源 revision、原始正文和摘要、变量及 engine/format 等解释信息。
- catalog 升为 `2026-09-06.1`，声明 engine/format 的允许值；本阶段仍保持 `none` / Python `str.format`。正文 marker 不再承担发布门禁，P0 的六项原字节门禁保持不变。
- 远端来源身份不符、同 revision 本地损坏/不同 payload 均产生独立 `revision_conflict`，不静默覆盖或修复；catalog 不匹配与本地摘要损坏可分别诊断。
- 差异在激活事务锁内相对当前 active LKG 计算；与代码默认值的差异单独列出。没有 active 时，缺少的全部 key 被报告为变化，不能把默认值充当历史 active。

P2 的逻辑身份仍使用来源 revision 和 `prompt_bundle/fusion` 归因；不修改任何历史 Run identity 或正文快照。新 Run 的 catalog 身份使用新声明版本，已有 Run 保持自己的冻结来源。

## 未来另行授权后的发布顺序

1. 沿既有 workflow 锁定候选镜像 digest，先完成数据库结构迁移。
2. 核验旧容器镜像身份后，用原 image ID 和原数据库/Prompt 配置运行独立冻结进程，抓取完整 LKG 正文和来源身份；运行、停止及反复重启的旧容器均支持。
3. 候选一次性容器读取当前 published 原始包，验证完整性、P0 门禁和部署前 effective map 逐 key UTF-8 字节相等。在同一 advisory lock 的事务内新建并激活 v2，追加桥接回执，保持 v1 不变。
4. 任一步失败即停止在服务替换之前。预置使用必要的已解析环境变量，不把 shell 配置文件当 Docker env-file，不启动 lifespan。
5. 新服务启动期只读已预置的 v2，随后正常同步；health smoke 查询实际存储键并调用真实冻结入口，不能用旧 v1 行的存在替代验收。
6. 首个 P3a 发布不自动停用 v1，以便尚依赖 v1 的上一版代码仍可按既有流程回滚。全部旧 worker 退出，且可用的代码回滚锚点也支持 v2 后，才可另行调用受审计的 `retire_legacy_bundle()` 停用旧行；只改 active 标志，不改正文、不删除数据。

`seed` 和 `retire` 的回执使用保留 namespace 内独立 key；通用管理员写接口仍不能修改该 namespace。旧 v1 永不成为 v2 治理回滚目标。P3b 再添加专用 hold；P3c 另交付可独立构建的双 profile 桥接版本与 Jinja 收口版本，不能一次合入最终树后假设中间提交已经发布。

## 验证记录

测试先行：完整性首轮 10 个失败用例、事务首轮 4 个失败用例、桥接首轮 6 个失败用例均在实现前观察到；随后增加 CLI 与部署命令失败路径。测试使用 mock HTTP、真实 SQLAlchemy/SQLite 事务与默认 `expire_on_commit=True`，没有调用真实模型或服务。

复审中修复了三项可达阻断：事务关闭后读取过期 ORM 回执对象，改为提交前生成并返回标量 ID；shell 配置中的引号/变量展开被 Docker env-file 改变，改为只传已解析的必要环境变量。旧容器停止时无法 docker exec 抓取基线，改为核验旧 image ID 后运行原代码的一次性冻结进程。带引号配置、三种旧容器状态及身份变化、预置失败不替换服务、事务失败不留半个 v2 都有回归。

最终本地验证：全量 pytest `3983 passed, 2 skipped, 4371 subtests passed`；CI 使用的 unittest discovery `3044 tests, OK (skipped=2)`；仓库级契约 `67 tests, OK`。Ruff check、改动 Python 文件 format check、架构检查、shell 语法/静态检查及 diff check 通过；架构仅有四项既有测试覆盖提醒。工作树已由两个独立代理复查，修复上述三项后未遗留可达 P0/P1。正式提交 HEAD 复审和 PR CI 状态记录在 PR。当前报告不宣称已合并、已部署或已经取得真实 PostgreSQL 多 worker、dev 模型、浏览器验收；这些环境操作均未在本次执行。
