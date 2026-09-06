# P3c 独立桥接版本：冻结引擎与持久迁移阶段

日期：2026-09-06，时区 Asia/Shanghai。基线：P3b `97da2d226b2d910bf76428fb8c23d12f25833d47`。本版本必须先独立构建、发布并验收，之后才允许部署最终 Jinja2 收口版本；一次最终 HEAD 发布不能代替此桥接。

## 两种完整契约

旧完整 v2 的 `catalog_version=2026-09-06.1 / template_engine=none` 保持原字节和原 checksum。新完整 v2 使用 `2026-09-06.2 / jinja2`。两者均为 11 项 `format=text`，不接受任意混合引擎。来源 revision 仍按官方原始材料 canonical 重算，本地 checksum 仍包含 catalog、format 和 engine；同 revision 不能覆盖既有不一致 payload。

桥接版代码默认值仍为旧引擎，旧 P2 Run 的身份和历史正文不修改。`PromptTemplateSnapshot` 冻结 format/engine，标题、推荐问题、文件分析与附件包装统一从同次解析返回的元数据选择 renderer；运行中换包不改变已经冻结的 engine。新 LKG 身份使用其实际 catalog，不把旧 payload 重标为新 catalog。

Jinja2 显式固定 `3.1.6`，使用 `SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)`，与规格所记录的 PromptHub 配置一致；变量集合用 `meta.find_undeclared_variables` 精确比对。七项无变量正文禁止 Jinja 语法标记，保持直接消费正文的语义。配置与沙箱行为参见 [Jinja 官方 API](https://jinja.palletsprojects.com/en/stable/api/) 和 [Sandbox](https://jinja.palletsprojects.com/en/stable/sandbox/)。

P0 按显式引擎选择两份独立原字节基线，六个既有门禁 key 不变。Jinja2 的 `file_content_enhancement` 使用独立受审阅的占位符字节；不能把旧/新模板渲染相等当作原模板字节相等。Jinja 激活和任何引擎阶段提升均要求已 attested，旧引擎的未 attested 过渡规则不扩展到 Jinja。

## 持久阶段与受管发布

迁移 `f5a7d0e3b812` 只追加 `prompt_bundle_engine_transitions` 及触发器。没有转换事件表示 legacy；随后只能追加 bridge，再追加 jinja2。事件禁止更新和删除，代码回滚不 downgrade。策略提升与 active/P0/held target 的校验使用原 Prompt advisory transaction lock；PostgreSQL 要求 READ COMMITTED，触发器为 VOLATILE。

| 持久阶段 | 允许激活 | 所有正向/失败回滚目标必须通过的契约 |
|---|---|---|
| legacy | 完整旧引擎 v2 | 旧引擎校验、冻结、渲染 |
| bridge | 完整旧引擎或完整 Jinja2 | 两者的完整独立探针 |
| jinja2 | 仅完整 Jinja2 | Jinja2 完整探针 |

数据库触发器限制新激活，包括旧代码重新激活的路径；inactive 历史和旧 Run 留存。进入 hold 也不能越过最终阶段的激活约束。进行中 Run 不读取新策略改变 engine；既有 bundle TTL 不在本阶段调整，运行中 worker 的新 Run 收敛仍须单独验收，不能由一次性镜像探针代替。

`prompt-engine-transition.yml` 与 `deploy-dev.yml` 共用 `fusion-dev` 非取消 concurrency，避免旧策略 preflight 已通过但尚未替换容器时并发提升。它仅在 master、dev 受管 runner 上执行，默认 dry-run，只有显式 apply 才追加事件。

发布器逐一核对当前 Docker host 的所有受管 API worker、不可变 repository digest 和实际 image ID；任何可自动恢复的停止/异常受管容器都阻止提升。API 未定义 Docker HEALTHCHECK，因此使用容器内真实 HTTP `/health` 核验。每个 worker 执行当前发布器注入的完整契约探针和真实 active freeze，之后使用当前已核验镜像 ID 再运行独立 oneshot，明确保存兼容回滚 digest。再次核对 worker/config 身份后，锁内核对 expected revision 并追加事件。

该入口覆盖现有单 Docker host 的受管 Fusion stack。其他 host、无受管标签的手工启动、旧 workflow 重跑和数据库超级用户直接改写不属于本发布协调范围；扩大部署拓扑前必须相应扩展消费者清单。

## 未来另行授权后的顺序

1. 在 legacy 阶段应用迁移、独立部署本桥接镜像，实际 active、旧 Run 与新 Run 原字节保持不变；失败时仍可回滚 P3a/P3b。
2. 通过真实消费者和新兼容锚点探针，将阶段提升为 bridge。首次桥接部署遗留的旧引擎回滚镜像不能继续作为有效锚点；迁移事件保存本次独立核验的 bridge digest。
3. 用 `prepare_jinja_prompt_baseline.py --published-json 原始响应 --out 新文件` 准备完整提案。工具保留原始 raw variables 和七项原正文，只转换可证明的简单占位符；复杂格式、Jinja 字面语法或换行导致的渲染差异会拒绝。提案没有虚构的新 source revision，只有源 revision 和逐项原/新正文摘要。
4. 另行授权在 PromptHub 发布完整 Jinja 基线，获取真实 published revision，验证 canonical、完整性、P0、新 Run 及 PromptHub 预览与 Fusion 的真实 UTF-8 往返；hold 期间候选仍只存 inactive。
5. 核对 active 与现存 held target 均为完整 Jinja；若仍 held 旧引擎则停止，不能自动解除。将持久阶段单向提升为 jinja2，再部署独立最终收口镜像。兼容 bridge 镜像仍可作代码回滚锚点，旧引擎包不能重新激活。

## 本地证据与剩余环境门禁

目标测试覆盖双引擎完整包、混包/format/变量/语法负向、独立 Jinja 配置渲染原字节、冻结引擎、P0 两代基线、阶段不可跳过、旧激活/hold 拒绝、追加审计、worker 身份漂移、停止旧容器、真实 HTTP 检查入口及 workflow 协调。独立复审发现的未 attested 提升、停止旧容器遗漏和不存在 Docker Healthcheck 的阻断均已修复。

本地实际执行的是 mock HTTP/模型、SQLite、独立 Python 进程和 fake Docker。PromptHub 真实往返、生产 PostgreSQL 并发锁等待、多 worker 收敛及 bridge/final 实际发布尚未执行。尝试只读获取规格引用的 PromptHub 历史源码返回 404，因此配置来源为既定规格和官方 Jinja 文档，不能据此声称已重新验证远端实现。没有启动服务、写 PromptHub、改变 GitHub 变量、部署或删除数据。

本地后端全量 `4072 passed, 2 skipped, 4389 subtests passed`，unittest `3044 tests, OK (skipped=2)`，根目录契约 `67 tests, OK`。其后追加了策略提交失败与 PostgreSQL SQL 生成检查两项回归，目标与 CI 入口契约共 `19 passed`。Ruff、改动格式、架构和 diff 检查通过。独立工作树运行时复审已关闭发现的 P1，正式提交后仍核对 exact HEAD。
