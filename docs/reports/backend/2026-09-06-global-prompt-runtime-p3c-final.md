# P3c 最终收口版本：仅 Jinja2

日期：2026-09-06，时区 Asia/Shanghai。基线是独立桥接提交 `9fc318b693bb7a55fc70577708e5e32ed19f815d`，两名独立代理已完成其 exact-HEAD 复审。桥接构建、环境部署和真实往返仍须分别完成；本最终代码提交不能代替中间发布。

## 最终代码行为

runtime catalog 固定为 `2026-09-06.2 / format=text / template_engine=jinja2`，移除旧 `str.format` 校验和渲染。四个默认模板与桥接版本的独立 Jinja2 基线逐字节相同；七项无变量正文没有改变。代码默认来源的 effective revision 因 catalog/正文改变而产生新身份，历史 P2 Run 的原身份、正文和数据库记录不更新。

旧完整 v2 继续物理保留，但最终代码拒绝其发布校验、stored 读取、渲染和新 hold。独立 JSON 夹具保存桥接版本校验通过的完整旧原始材料与 payload，并固定 SHA-256，负向测试不能依赖损坏或重标后的旧包。已经保存的旧 Run 正文仍按既有只读历史协议返回。

四处消费者使用冻结 template_engine；P0 六个 key 的原字节门禁继续有效，只选择最终 Jinja 基线，不接收旧基线或渲染等价替代。Jinja 激活要求已 attested，未 attested 的正常过渡规则不能用于绕过独立引擎迁移。

apply 启动在完整 LKG 与 P0 校验后，从数据库锁内验证持久阶段已为 jinja2。发布 preflight 同样按阶段执行全部所需契约：本最终镜像在 legacy/bridge 阶段被拒，不能趁当前恰好是 Jinja active 就跳过桥接。收口后的代码回滚仍可使用已验收的双引擎 bridge 镜像；数据库继续禁止旧引擎重新激活，不降级 schema，不删除 hold 或引擎审计。

桥接专用的旧/新双基线模块及离线转换提案脚本只留在独立桥接提交，最终树移除。旧初始化迁移 CLI 也在创建 Admin client 前关闭，避免最终默认 Jinja 正文被旧脚本标为 none 后重新发布；原初始迁移算法保留供历史回归。

## 分阶段交付与环境门禁

| 阶段 | 本地独立提交 | 作用 |
|---|---|---|
| P3a | `90962a5c05368d6fbe038c95de6de5bc59da310a` | 完整 v2、canonical/local checksum、v1 → v2 原子预置 |
| P3b | `97da2d226b2d910bf76428fb8c23d12f25833d47` | 持久 hold、追加审计、代码回滚保护及 apply 模式约束 |
| P3c bridge | `9fc318b693bb7a55fc70577708e5e32ed19f815d` | 独立双引擎构建、两代原字节基线、单向引擎阶段 |
| P3c final | 本报告所在独立分支 | 仅 Jinja2 运行时及持久收口启动门禁 |

后续受管发布顺序见 [桥接报告](2026-09-06-global-prompt-runtime-p3c-bridge.md)。必须先独立部署和验收 bridge，完成消费者/兼容锚点检查，提升 bridge，另行授权发布完整 Jinja 基线并做真实往返，再锁定 jinja2 阶段，最后部署本版本。若仍 held 旧引擎，不能自动解除或强行封闭。

本次只实施版本库代码和测试。没有启动本地服务、迁移真实数据库、写 PromptHub、修改 GitHub 变量、合并或部署。实际 PromptHub 管理端与 Fusion 的 UTF-8 往返、PostgreSQL READ COMMITTED 并发、多 worker 收敛和真实新 Run 验收尚未执行；mock 与 SQLite 不替代这些证据。

## 验证说明

bridge 中证明旧/新双引擎可运行的正向用例保留在独立桥接提交。本树将相应契约替换为最终版本应有的旧引擎拒绝、Jinja 正向和未收口启动拒绝；不是通过放宽断言维持旧引擎。同步正向用例使用已完成 Jinja 基线 attestation 的环境，同时新增未 attested 时零写入的负向。版本冲突、损坏拒绝、hold 原子性、冻结身份及现有业务回归继续执行。

最终本地后端全量 pytest 为 `4071 passed, 2 skipped, 4389 subtests passed`（59.07 秒），unittest 为 `3045 tests, OK (skipped=2)`（49.276 秒），仓库级契约为 `67 tests, OK`（17.667 秒）。Ruff、架构检查及 diff 检查通过；架构检查保留四项既有警告。两名独立代理对最终工作树复审均未发现可达 P0/P1，其中一名另行执行 53 项目标测试，并用 SQLite 验证 bridge 阶段拒绝启动、jinja2 阶段放行。正式提交的 exact-HEAD 复审另行核对。

本地日志为 `/private/tmp/fusion-p3c-only-full-final.log`、`/private/tmp/fusion-p3c-only-unittest.log` 和 `/private/tmp/fusion-p3c-only-root.log`。这些是本地证据，不能等同于 PR CI、真实 Docker 构建或环境验收。P3a 分支已推送，但 PR 创建被自动审批拒绝；P3b、P3c bridge 和本最终分支尚未推送。具体 GitHub 外发确认尚未收到。

## 最新 master 对齐

最终分支已重放到包含 PR #46 跨平台冻结夹具字节预检的 `master@2ab18648a05ac9e5f4252406954be808ff24340c`。Linux 与 Windows 构建入口继续在任何 Docker build 之前执行该预检，并把已退役的 bridge/Jinja 提案测试替换为最终 `test_prompt_template_engine.py` 与 `test_prompt_jinja_only.py`。对齐后目标回归为 `62 passed`、`54 subtests passed`；Ruff、冻结夹具摘要、Bash 语法和 `git diff --check` 通过，完整容器门禁由更新后的 PR CI 复核。
