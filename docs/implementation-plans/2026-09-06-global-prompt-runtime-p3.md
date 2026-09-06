# Issue #34：P3 完整性、回滚与引擎桥接实施计划

日期：2026-09-06，时区 Asia/Shanghai。基线：`origin/master@ed3a789365a46c5724420d2ffec18d6f06040e81`。

## 已定稿迁移决策

用户已选择旧 schema v1「逻辑放弃、物理暂留」：不推断原始字段、不做字段回填迁移、不实现长期 v1 兼容读取。v2 必须从 PromptHub 完整原始 published bundle 新建并严格验证。短期桥接应保证正常切换期间没有代码默认值窗口；完成后 v1 inactive，最终 runtime 不读 v1。既有 P2 Run 身份与正文快照不修改；旧 v1 不作为新 hold 的回滚目标。

P3b 继续实施独立数据库 current-state hold 和 append-only transition events，为后续 v2 版本提供即时回滚。P3c 使用独立桥接阶段切换 sandboxed Jinja2 完整基线，不放宽 P0 原字节门禁。

本次交付边界是独立 PR 复审：允许代码、测试、提交、push、PR；不合并、不部署 dev/生产、不写 PromptHub、不改 GitHub 变量、不删除数据。后续发布操作只交付可审阅脚本与验收步骤，不在本次执行。

## 数据与无默认值窗口的桥接

1. v2 使用与旧 `prompt_bundle/fusion` 隔离的存储键，source revision 仍是官方 canonical 摘要；逻辑 Run 归因不因物理存储键变化而改写。这样相同 revision 的 v1/v2 可以并存，旧同步器也无法取消 v2 active。
2. 旧 worker 仍在运行时，从完整 published 包校验并预置 v2；保留 v1 active。预置必须逐 key 核对旧有效正文及 P0 门禁，不从 v1 推测 raw variables；失败不影响两侧 active。
3. 候选 runtime 在启动前确认 v2 已就绪，最终热路径只读 v2。替换全部旧 worker，且代码回滚锚点也已支持 v2 后，再显式停用 v1；不能仅凭同一 advisory lock 或固定等待时间宣称旧 worker 已退出。首个 P3a 发布保留 v1 active，避免尚依赖 v1 的上一版回滚镜像失去数据。
4. 现有发布 smoke 固定查询旧键，须改成候选代码所声明的存储键和 catalog 数量。预置入口必须为纯校验/专用写入路径，不能复用当前会取消 active 的 `mode="shadow"` 行为。
5. 业务正常的 bridge 与实际 DB 故障时的代码默认值灾备区分验收。后者保持既有 P2 完整来源与身份语义，不用移除故障降级来伪造迁移成功。

## 分阶段实现

| 阶段 | 内容 | 证据 |
|---|---|---|
| P3a | 原始 variables、canonical source revision、v2 checksum/catalog、冲突分类、active diff、专用预置与旧格式停用边界 | 独立夹具、原始顺序、损坏和冲突拒绝、事务失败、同 revision 新旧隔离、无默认值窗口 |
| P3b | 持久 hold、专用 admin 操作、锁内状态读取、不可变事件、同步诊断 | 事务回滚、重复操作、权限、远端失败下本地回滚、重启与多个 worker、陈旧缓存、release 后跟随 |
| P3c | 显式 Jinja2 依赖、冻结 engine、严格变量/format/engine 校验、完整基线的独立桥接与收口 | 双方独立契约 UTF-8 往返、四模板及七无变量模板、P0 启动矩阵、运行中换引擎仍冻结、回滚兼容边界 |

实施沿用已有 P3a → P3b → P3c 分阶段顺序；各可发布阶段必须有独立、可构建和复审的代码状态，不假设一次 HEAD 发布可替代中间桥接。Jinja 阶段的旧/新基线分别验证原始字节与渲染契约，不把渲染等价当作 P0 原字节相等。

保持 P1 section identity、P2 分类前冻结和原子身份屏障、正文 degraded、新 attempt 独立解析及轻量投影不变。P4 定时/TTL/收敛与 P5 扩 catalog/英文化/分类器不提前实施。移除正文 marker 发布门禁按规格执行，不改变 section identity。

## 测试与复审

先写失败测试，再实施最小改动。单元测试使用独立 canonical 夹具；事务测试使用真实 SQLAlchemy 事务，PostgreSQL advisory lock 的并发保证另列数据库契约，不把忽略 filter 的假 query 当成并发验收。

每个可交付 HEAD 运行目标和全量 pytest、Ruff、架构及 diff 检查，并由未参与该实现的代理复审。模型调用一律 mock，不启动本地服务、Docker 或 dev 子进程。

修改前基线验证已通过：59 tests、23 subtests，涵盖 bundle、快照、同步、effective map 与 catalog 门禁。实现及最终验证另记入执行台账和阶段报告，不以本计划代替完成证据。

## P3b 实施补充

为避免代码回滚到不识别 hold 的 P3a 后被旧同步器覆盖，增加持久数据库触发器保护与目标镜像真实冻结 preflight。hold schema 生效后，受当前发布器管理的目标最低能力固定为 v2，不随当前 following/held 状态放宽到 v1；代码回滚保留 schema 和审计。事务显式使用 READ COMMITTED 可见性前提，其他隔离级别 fail closed。具体接口、失败路径和环境验收边界见 [P3b 报告](../reports/backend/2026-09-06-global-prompt-runtime-p3b.md)。

## P3c 桥接与收口约束

增加 legacy → bridge → jinja2 单向持久阶段和数据库激活保护。桥接代码保留旧 v2 原样读取及代码默认值，只在独立完整新 catalog 中接受 Jinja。阶段提升与部署共用 fusion-dev concurrency，必须验证全部策略契约、真实 worker、原字节 active 和新兼容回滚锚点；引擎声明字符串不能代替消费能力。最终只支持 Jinja2 的代码另建分支/提交，在持久阶段已收口后才能启动，不能跳过独立桥接构建与发布。细节见 [P3c 桥接报告](../reports/backend/2026-09-06-global-prompt-runtime-p3c-bridge.md)。
