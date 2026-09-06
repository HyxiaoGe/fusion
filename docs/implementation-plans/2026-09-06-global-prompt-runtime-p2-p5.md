# Issue #34：P2–P5 分 PR 实施计划

基线：`origin/master@3c737caf41399b7868bb0e8ec24da2a2a3d6249b`。北京时间 2026-09-06 核对 issue #34 当前正文与评论、PR #36/#37、执行台账及全局 Prompt Runtime 规格。

## 契约与阶段边界

- P0、P1 已合并；正文保持中文是 P1 保持字节不变的结果。台账中 P1 的本地状态是历史快照，后续发布证据单独补记。
- Codex 主开发；独立代理只做代码与规格复审，不由 Claude Code 代开发。
- 使用独立 `codex/` 分支，不覆盖现有工作区。每个阶段独立 PR、目标测试、全量测试、Ruff、架构检查、diff 检查及当前 HEAD 独立复审。
- 生产部署、数据删除、权限与密钥修改另行确认。实现、提交、push、PR CI、合并、dev 发布、真实浏览器验收分别记账。
- 不启动本地 Fusion 服务。登录态验收仅复用已打开的匹配 Chrome 标签。
- 不修改自然盲测期望来迁就实现，不为城市样本添加特判，不使用指定工具或指定答案的提示语。

## 分 PR 顺序

| PR 阶段 | 改动与依赖 | 必须取得的证据 |
|---|---|---|
| P2 | 两层不可变快照；分类前原子持久化身份；所有 Run 阶段使用冻结来源；continuation 传 section identity | 中途切包及并发隔离；身份写入失败零模型调用；正文 degraded；新 attempt 独立版本；无 PromptHub 热路径 HTTP |
| P3a | canonical `source_revision`；保留原始有序 variables；独立 local payload checksum、catalog version 与 revision conflict；active-LKG diff | 规范化摘要独立夹具、损坏/同 revision 冲突拒绝、active diff 正负对照 |
| P3b | DB current-state hold + append-only transition events；锁内激活与 hold 转换；专用治理操作 | rollback/hold 原子性；重启、多 worker、陈旧缓存不绕过；远端 5xx 可本地回滚；release 恢复跟随 |
| P3c | sandboxed Jinja2；显式依赖；4 个变量模板语法迁移；重新发布完整基线 | 与 PromptHub 的渲染契约逐字节往返；shadow 校验；完整基线重新发布与生效版本验证 |
| P4 | interval 60 秒、TTL 30 秒；各 worker 轮询 + DB 互斥；收敛观测与告警 | dev 各 worker 新 Run 120 秒内收敛、超过 150 秒告警；热路径无 HTTP |
| P5a | 辅助生成模板扩容与英文正文；独立辅助调用的归因 | catalog 消费、变量和正文检查；两个模型自然回归 |
| P5b | 搜索上下文及工具 description；契约类 description 留在代码 | 工具权限/schema 不变；真实调用与引用来源检查 |
| P5c | 收尾与续写、产品结果事实边界、知识库 | limit/no-progress/repair/研究总结与新 continuation 版本证据 |
| P5d | 主链路、计划与研究；Skill 升 `1.1.0` 并更新 pin | Skill 加载、权限、终态；冻结后的动态研究消息归因 |
| P5e | 分类器最后迁移；taxonomy 从代码真值渲染；持久化 classifier fingerprint；通用可变事实判断 | 原始类别前后对比；决策层/error_type/duration/source_kind/effective_revision/fingerprint；至少两个模型 |

P3c 默认仅修改 Fusion 渲染器以对齐既有 PromptHub 契约。若实测显示 PromptHub 必须改代码，另开该仓明确范围的分支/PR；不在 Fusion PR 中宣称跨仓工作已经完成。

P5 发布顺序固定为先发布完整新 catalog bundle、再部署对应代码；旧代码拒绝新增 slug 并保留 LKG。输出侧 sanitizer 是 P5 生产发布硬门禁：确定性 fixtures 命中 100%、误删 0；holdout 在标定前冻结，2 个支持工具调用的模型 × 每样本 3 轮 × 中英文各 30 条自然样本，漏删 0、误删率 ≤ 1%，每次误删人工复核。holdout 输出不得回流短语表。

## P2 执行步骤

### 1. 冻结 Bundle 与原子解析

创建 core 的不可变 `PromptBundleSnapshot` 与条目类型；factory 一次读取完整 active LKG，无有效包则整包代码默认值。按规格的 canonical JSON 计算代码默认值 effective revision。catalog 仍为现有条目，不切换引擎或英文正文。

测试先证明：A 包冻结后切 B，所有 getter 仍读 A；新快照读 B；单 key 损坏使整包回到代码默认值；输入 payload 后续被修改不影响快照；ContextVar 在异常、并发 task 与 worker thread 中隔离。

### 2. 分类前身份屏障

`runner.py` 在创建 classifier deadline gate 之前冻结 Bundle、分配 Run ID、原子写 `AgentSession.run_config` 的身份。分类后只补齐该 Run 的执行配置，不能重新解析或重写已冻结身份。分类器只消费传入的内存正文，不在 1.5 秒预算中读 DB。

测试先证明：身份写入异常时分类模型与主模型均未被调用；分类失败或取消不会留下没有终态的已启动 Run；同 Run ID 不允许换身份，新的 retry/regenerate/continue 保留 lineage 并使用自己的版本。

### 3. Run 最终消息与阶段读取

在 `prepare_agent_loop_messages()` 中由同一 Bundle 派生 `RunPromptSnapshot`，携带最终 system messages、section identities 与 fingerprint。Run 生命周期内的 getter 从该快照所属模板读取，覆盖工具轮、所有总结与语言 finalize。代码控制的动态事实/日期/权限继续由代码生成。

`extra_system_prompts` 保留调用参数名称，但内容改为受控 section identity；continuation 调用方不再预先求值正文。未知 identity fail closed，避免正文被误认为可信身份。

测试先证明：continuation 在 caller 与准备阶段之间切包仍只取新 Run 的冻结版本；工具轮与 limit summary 中途切包不漂移；正文快照写入失败只产生显式 degraded，实际模型输入仍取冻结内存。

### 4. 独立辅助调用与审计

标题与推荐问题每次独立调用都使用自己的冻结解析结果和 `source_kind + effective_revision` 元数据；异步子任务不能继承主 Run 的旧 Prompt 归因。主 Run 的轻量身份进入持久化配置与安全只读投影，完整正文仍沿用现有快照表。

### 5. 验证与交付

目标命令从 backend 执行，只借用已有完整依赖环境的解释器 `/Users/sean/code/fusion/fusion-api/.venv/bin/python`，不启动服务：

```bash
DATABASE_URL='sqlite:///:memory:' /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest test/test_prompt_bundle.py test/services/stream/test_agent_loop_wiring.py test/services/stream/test_agent_loop_lifecycle.py -q
DATABASE_URL='sqlite:///:memory:' /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest test/ -q
/Users/sean/code/fusion/fusion-api/.venv/bin/python -m ruff check app test
/Users/sean/code/fusion/fusion-api/.venv/bin/python scripts/check_architecture.py
git diff --check
```

基线目标集合已通过：`54 passed + 32 subtests`。新增契约按 RED → GREEN 记录，format 只检查本次修改文件。目标与全量通过后提交并取得当前 HEAD 独立复审，推分支和单独 P2 PR，等待其 CI。未取得该阶段运行验收前不宣称 issue #34 完成。
