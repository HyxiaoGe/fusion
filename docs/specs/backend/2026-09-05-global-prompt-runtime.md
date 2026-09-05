# 全局 Prompt 运行时：catalog 治理、Run 冻结与英文化（设计规格）

对应 issue #34。本文是三方复审（issue 作者 / Claude Code / Codex）达成共识后的实施规格，供再次复审；定稿前不修改 Prompt 正文、不扩 catalog、不改业务代码。

复审基线 `origin/master@ef43326`。下文所有代码位置均按该基线核对。

## 与既有规格的关系

本文取代 [2026-08-26-system-prompt-assembly.md](2026-08-26-system-prompt-assembly.md) 中「主聊天基础、工具边界、计划规则与续跑模板切为代码来源」的决策。该决策在当时是有意为之，但它与 [2026-07-10-prompthub-migration-design.md](2026-07-10-prompthub-migration-design.md) 建立的 11 项 catalog 并存，导致 catalog 的「声明」与「消费」脱节（见下节）。本文把这两份规格的边界重新统一。

`2026-07-10` 建立的 published bundle → 本地 LKG → 代码默认值三级消费链继续有效，本文只扩展它的覆盖面并补齐冻结、身份与回滚语义。

## 已确认的范围

- PromptHub 是已发布全局 Prompt 正文的运行时事实源；仓库保留英文代码默认值用于冷启动与灾难恢复。
- 管理员可只编辑单个 Prompt，但发布产物必须是完整 bundle；Fusion 只做完整版本的原子切换，不允许部分生效。
- 冻结单位是**单次 Run attempt**。continuation / retry / regenerate 都是新 Run，允许使用新 revision。
- PromptHub 不可用、鉴权失败、超时、坏版本或 bundle 不完整时继续使用最后可用版本（LKG）。
- 聊天热路径不得发出 PromptHub HTTP 请求。
- 联网判断改为通用原则：依据回答是否依赖可变外部事实，而非场景硬编码。
- 全局指令统一英文；用户内容与最终回答语言仍由用户请求决定。
- 跨 worker 允许最终一致，不追求秒级原子切换。

## 结构性前置：声明即消费

当前 `PROMPT_SPECS` 声明 11 项（`app/core/prompt_catalog.py:17-43`），实际经 `resolve_prompt_template` 消费的只有 6 项。`app_identity`、`tool_usage_contract`、`no_tool_network_boundary`、`no_vision_file_boundary`、`continuation_system` 的 getter 直接返回代码常量（`app/ai/prompts/agent_loop.py:269-300`），且 `test/test_prompt_runtime_templates.py:18-32` 用 `side_effect=AssertionError` 反向锁定了这一行为。

后果是：管理员编辑这些条目时 bundle 校验通过、revision 变更、`runtime_config_versions["prompt_bundle/fusion"]` 记录新版本（`app/services/stream/agent_loop_lifecycle.py:609-611`），但模型行为不变。这是「发布成功但静默空转」，比同步失败更危险，且 trajectory 会给出错误的因果归因。

规格要求：

- catalog 是单一事实源。每个 spec 必须注册真实消费它的 resolver，缺失则启动期 fail-fast。
- 新增契约测试证明 catalog 每一项都能被真实注入路径消费。
- `app/core/prompt_bundle.py:55` 错误文案中的 `11` 与 `test/test_prompt_bundle.py:38` 的 `assertEqual(len(PROMPT_SPECS), 11)` 改为由 catalog 派生。
- 重写 `test_prompt_runtime_templates.py:18` 的反向断言。

### P0 的「行为零变化」前置条件

P0 会让此前直接返回代码常量的 5 个 getter（`app_identity`、`tool_usage_contract`、`no_tool_network_boundary`、`no_vision_file_boundary`、`continuation_system`）首次开始消费当前 active bundle。若管理员此前已在 PromptHub 编辑过这些条目，那些静默无效的正文会在 P0 上线瞬间生效。「仍为 11 项、仍为中文」不足以保证行为不变。

因此 P0 apply 前必须满足下列前置条件之一，否则 fail closed，不得进入 apply：

- **基线优先（推荐）**：先从代码默认值生成一份基线 bundle 发布到 PromptHub 并固定为当前 published revision，使 bundle 与代码常量按构造即一致；或
- **逐 key 字节校验**：逐条校验待激活 bundle 的每个 key 与对应代码常量**字节一致**（UTF-8 原始字节，不做 strip / 换行归一化），任一不一致即拒绝进入 apply 并告警。

该校验写入 P0 验收，不作为实施者的可选项。

## Section identity 与 marker 契约

当前 marker 是中文正文子串，并被三处业务逻辑当作程序控制信号消费：

| 位置 | 依赖方式 | 英文化后的失效后果 |
|---|---|---|
| `app/services/stream/limit_summary.py:185-198` | `terminal_control_markers` / `deep_research_control_markers` 子串匹配 | 收尾阶段无法剔除工具契约，模型继续输出工具协议 |
| `app/utils/user_visible_content.py:42-57` | `_INTERNAL_REASONING_CONTROL_MARKERS` 抄录 prompt 原文措辞 | 内部控制规则泄漏进用户可见 reasoning |
| `app/services/chat/model_call_language_policy.py:15` | `content.replace(VISIBLE_RESPONSE_LANGUAGE_PROMPT, "")` 精确子串删除 | 语言契约重复叠加 |

另有 `limit_summary.py:168` / `:171` 用 `X not in prompt` 判断是否追加，同样是正文耦合。

这三处**不是同一类问题**，必须拆成输入侧与输出侧两条独立契约。前两处（`limit_summary`、`model_call_language_policy`）操作的是 Fusion 自己组装的 system message，有可关联的结构化身份；`user_visible_content` 操作的是**模型已经生成的 reasoning 文本 / SSE chunk**，来源 system message 的 metadata 早已丢失，模型复述或改写后不存在任何可关联的 `section_id`。把两者写成同一条「按 section_id 工作」是错的。

### 输入侧：结构化 section identity（确定性契约）

- **载体**：内部 typed message 或 sidecar metadata。必须贯穿所有 `inject_*` 变换，并在发送给 provider **之前剥离**。
- **禁止**把 section id 以任何形式写回 prompt 正文——那只是把中文 marker 换成新 marker，正文耦合原样保留。
- 组装、去重、收尾删除一律按 section identity 判定：`limit_summary.py:185-198` 的终局契约清理、`model_call_language_policy.py:15` 的语言契约幂等删除与追加、`agent_loop_request_prep.py:581` / `:622` / `:648` / `:662` 的去重，全部改为按身份而非正文子串。
- 同时消除 `limit_summary.py:168` / `:171` 的 `X not in prompt` 子串判断。
- 验收：`test_limit_summary` / `test_agent_loop_request_prep` 在「正文任意替换、section identity 不变」的参数化下必须全绿。这是确定性验收。

### 输出侧：用户可见 reasoning 净化（独立 best-effort 防线）

- 这是独立防线，**不承诺**正文任意替换后仍能靠 section identity 清除模型复述。
- 可选实现方向（本期择一定稿）：稳定协议标签 / 签名，使模型复述可被结构化识别；或产品层不暴露 raw reasoning。
- 必须单独定义失败语义（漏删、误删各自的影响面与降级行为）与单独验收，不得并入输入侧的确定性验收。
- 已知既有风险：`user_visible_content.py:42-57` 中 `"according to the rules"` 一类过泛英文短语，在英文化后误删正常 reasoning 段落的概率会上升（该函数删除 marker 到下一空行的**整段**，`:153-158`）。此项纳入输出侧防线的回归观察。

## Run attempt 冻结语义

`RunAttemptKind` 为 `Literal["initial", "retry", "regenerate", "continue"]`（`app/services/agent/session_cache.py:23`）。续跑经 `run_attempt_kind="continue"` + `previous_run_id` 创建新 `AgentSession`（`app/services/chat_service.py:791-792`），因此是新 Run。

当前 `prompt_snapshot`（`agent_loop_request_prep.py:497-506`）只写入 `agent_system_prompt_snapshots` 供 trajectory 只读投影（`trajectory_query_service.py:174` / `:285`），没有任何路径回读它重建消息。冻结只有观测、没有执行读取源。

规格要求：

- Run attempt 启动时一次性解析并冻结：bundle revision、每个 key 的 slug / version / 正文、最终 system messages 与 fingerprint。
- 该 Run 内所有阶段——tool round、plan synthesis、limit / no-progress / plan-repair / research-evidence summary、语言策略 finalize——只从冻结上下文读取，不得重新解析当前 active bundle。
- 新的 continuation / retry / regenerate Run 独立解析新 active bundle，记录自己的 revision，并保留 `previous_run_id` 使版本切换可审计。
- 标题、推荐问题等独立 LLM 调用记录自己的 Prompt revision，不伪装成沿用原 Run 快照。

两项必须在实现中显式处理：

1. **`extra_system_prompts` 的解析时机。** `get_continuation_system_prompt()` 当前由调用方在 `chat_service.py:794` 求值后传入，而新 Run 的解析发生在下游 `prepare_agent_loop_messages`（`agent_loop_request_prep.py:463-494`，`extra_system_prompts` 在 `:467-468` 被原样 yield 成 section）。两点之间若 bundle 翻页，会出现同一个新 Run 内段落来自不同 revision 的拼接。`extra_system_prompts` 改为传 section_id 列表，由 Run 启动时的同一次冻结解析统一渲染。

2. **续跑的混合继承是有意为之。** `resolve_continuation_limits` / `resolve_continuation_plan_mode`（`app/services/agent/continuation.py:44` / `:56`）从上一个 Run 的 `run_config` 继承 limits 与 plan_mode，但 prompt revision 不继承。因此可能出现「继承上一个 Run 的 `plan_mode` 契约，却使用新版本计划控制正文」的组合。这是刻意的，不是缺陷，实现时不得「修正」。

## Bundle 身份、完整性与回滚

### 身份与冲突

`revision` 当前由 PromptHub 下发（`app/services/external/prompthub_client.py:96`），Fusion 只校验它是 64 位 hex（`app/core/prompt_bundle.py:47-48`），从不校验它与内容的对应关系。

PromptHub 侧已有唯一确定的算法，不存在「摘要或标签」的二义性。已在 `HyxiaoGe/prompthub@5c9456e` 的 `backend/app/services/project_service.py:126-144` 独立核对：

```python
canonical = json.dumps(
    {
        "project_slug": project_slug,
        "prompts": [
            {
                "slug": prompt.slug,
                "version": prompt.version,
                "content_sha256": hashlib.sha256(prompt.content.encode("utf-8")).hexdigest(),
                "variables": prompt.variables,
            }
            for prompt in sorted(prompts, key=lambda item: item.slug)
        ],
    },
    ensure_ascii=False, separators=(",", ":"), sort_keys=True,
)
revision = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

摘要**只包含** `project_slug` 与按 `slug` 排序的 `{slug, version, content_sha256, variables}`；`name`、`status`、`format`、`template_engine`、`published_at` 均不参与。

规格要求：

- `source_revision` 即上述内容身份。Fusion 按同一 canonical contract 重算并要求**严格相等**，否则整包拒绝、告警并保持 LKG。
- 若 Fusion 另需覆盖 `schema_version` / `catalog_version` 的本地 payload 校验值，**另行命名**（如 `local_payload_checksum`）并明确参与字段，不得与 `source_revision` 混用。
- 正文的合法变化必须发布新 revision，不得静默修改已发布 revision。
- **不得**在普通同步中用远端覆盖同一身份的本地行。损坏行走隔离 + 受审计修复流程恢复。
- 此类拒绝必须是独立诊断状态（如 `revision_conflict`），不能与 timeout / 5xx / 校验失败一起打平进 `status: "error"` + `last_error`（当前 `_record_error` 即如此，`app/services/prompthub_sync_service.py:249`）。否则需人工介入的身份冲突与可自愈的网络抖动在监控上无法区分。

**重算的两个实现前提**（当前代码不满足，必须一并修改）：

1. **必须保留原始 `variables`。** `prompthub_client.py:106-109` 现在把 `variables` 归一化成名字元组并**丢弃原始值**；而 PromptHub 的 `PublishedPromptResponse.variables` 类型是 `Any`（`backend/app/schemas/prompt.py:121`），摘要序列化的是原始结构。按现状 Fusion 无法重算出相等的 revision。客户端必须同时保留原始 `variables` 供摘要重算，归一化后的名字集合仅用于业务校验。
2. **重算对 `variables` 顺序敏感。** 摘要把它作为 JSON 列表序列化，顺序参与哈希；而 Fusion 现有校验用 `set(variables) == set(spec.variables)`（`prompt_bundle.py:_validate_prompt_item`）是顺序无关的。因此存在「业务校验通过但 revision 不等」的情形，重算必须基于原始有序值，不能基于归一化集合。

### 回滚 hold

`_reject_runtime_prompt_mutation`（`app/services/runtime_config_governance.py:213-215`）使 `prompt_bundle` 命名空间对 admin API 完全只读，旧 bundle 行保留但无接口激活。

仅增加「激活历史 revision」的接口不完整：apply 模式下，若 PromptHub 当前 published revision 的行已存在但非 active，下一轮同步会走 `_activate_row(rows, existing)` 重新激活（`prompthub_sync_service.py:178-182`）；若是新 revision 则 `for peer in rows: peer.is_active = False`（`:191-192`）。两条路径都会在一个同步周期内冲掉手工回滚。

规格要求本地回滚是受审计的持久 `pin/hold` 状态机。仅描述效果不足以实施——实施时会在「新增专用表 / 复用 runtime config / 只做接口内变量」之间分叉，因此下列各项在规格阶段定稿：

- **持久实体与作用域**：hold 是独立的持久化实体，唯一作用域为 `(project_slug, catalog)`，同一作用域至多一条生效记录。不得实现为进程内变量或接口局部状态。
- **状态与原子转换**：`following -> held -> following`。转换必须在单事务内完成，并与 bundle 激活复用同一把 `pg_advisory_xact_lock`，避免与并发同步交错。
- **进入 held**：原子地完成「激活指定历史 revision」+「置 held」，不允许出现「已激活但未 held」或「已 held 但未激活」的中间态。
- **held 期间的 scheduler 行为**：继续 shadow-fetch、继续校验、继续更新诊断与告警，但**绝不 activate**、绝不改写 active 行。
- **专用 admin 操作与权限**：hold 的进入与解除是专用操作，不复用通用 runtime config 写接口（后者对 `prompt_bundle` 保持只读）。权限等级与既有 admin 治理接口一致。
- **审计字段**：操作者、原因、目标 revision、进入时间、解除时间，均持久化且不可事后修改。
- **worker 读取方式**：所有 worker 从同一持久实体读取 hold 状态，读取路径与 bundle 缓存共用失效语义；不得依赖任一进程的本地状态。
- **解除 hold**：恢复跟随 PromptHub published revision；若期间 published revision 已变化，按正常同步路径激活最新版本。

管理 UI 是非目标。但**跨重启、跨 worker、解除 hold 后的行为必须进入自动化验收**，不得只做人工验证。

**hold 必须是数据库状态**，不得用 `PROMPTHUB_SYNC_MODE` 实现。该配置在 `app/core/config.py:442` 于 import 期读入 Settings，修改需重启或重新部署；而 hold 要在故障中即时生效并同时作用于所有 worker。用环境变量实现 hold 会把「不发代码即可止血」的核心诉求交还给发布流程。

### catalog 扩容的兼容性

`validate_stored_bundle_payload`（`prompt_bundle.py:80-90`）要求 `set(prompts) == set(PROMPT_SPEC_BY_KEY)` 精确相等。catalog 从 11 扩到 N 的瞬间，库中已有 LKG 整体失效，`_load_active_bundle_payload` 返回 `None`，全部走代码默认值，且 `get_active_prompt_bundle_revision()` 返回 `None`、trajectory 中 `prompt_bundle/fusion` 消失。

功能上安全（代码默认值兜底），但存在一段无 revision 可归因的窗口。这不是向后兼容，是硬切换。

规格要求：

- catalog 增加 `catalog_version`，使 `schema_version` 之外可区分「catalog 不匹配」与「payload 损坏」两类失效。
- 发布顺序固定为：**先在 PromptHub 发布完整 N 项 bundle，再部署新代码。** 旧代码会因多出 slug 整包拒绝（LKG 与行为均不变），新代码上线后首次同步即可激活。反序会产生 `interval + TTL` 长度的代码默认值窗口。

### 诊断口径

`_build_shadow_diff`（`prompthub_sync_service.py:133-153`）将远端 checksum 与 legacy `prompt_template` 命名空间 / 代码默认值比较，而非与当前 active bundle 比较。apply 模式下 legacy 写入已被禁止（`runtime_config_governance.py:216`），基线永远是代码默认值，英文化后每次同步都会把全部 key 报成 changed。

规格要求：`changed_prompt_keys` 改为与当前 active LKG 的 per-key checksum 比较；「与代码默认值的差异」另列字段。

## 多 worker 同步与收敛口径

现状：`PROMPTHUB_SYNC_INTERVAL_SECONDS` 默认 300 秒（`config.py:447`）；`_BUNDLE_CACHE_TTL_SECONDS = 60.0`（`prompt_bundle.py:25`）为进程内缓存并与同步周期串联；`Dockerfile:42` 为 `--workers 4`，`main.py:207` 在 lifespan 内 `start_scheduler()`，故每个 worker 各持一份 scheduler 与缓存，`_clear_prompt_caches()` 只清本进程。

收敛链为：发布 → 首个 worker 轮询到（≤ interval）→ 写库并清自身缓存 → 其余 worker 各自 TTL 过期后读到新行（≤ TTL）。

规格固定参数：

| 项 | 值 |
|---|---|
| 同步间隔 | 60 秒 |
| Bundle 缓存 TTL | 30 秒 |
| worker 轮询方式 | 保留各 worker 独立轮询 + 数据库互斥 |
| 收敛目标 | 健康状态下 120 秒内全部 worker 收敛 |
| 告警阈值 | 超过 150 秒 |

60 + 30 的理论上界为 90 秒，对 120 秒验收线保留 30 秒余量，可吸收调度抖动、数据库延迟与偶发漏拍。

leader-only 轮询需要额外的选主与故障接管逻辑，不是纯减法，本期不做，留作后续性能事项。

同一 Run 内 bundle 仍然原子；跨 worker 允许短暂最终一致。

## 分层边界

### 由 PromptHub 热更新的正文

主链路：应用身份、联网决策、工具调用一致性、无联网工具边界、无图片理解边界、语言一致性、计划控制（auto / on）、深度研究执行约束、当前日期段正文骨架、用户偏好包装语。

收尾与续写：非披露约束、计划综合、触顶总结、无进展总结、无工具证据总结、计划修复总结、研究证据总结、工具协议重试、续写规则、计划控制修正、计划执行修正。

搜索上下文：开场、信任边界、引用规则、后续读取规则。

工具 description：`web_search`、`url_read`（Fusion 自有部分）。

深度研究阶段：阶段控制各段、研究证据工作集、完成校验。

产品结果事实边界：`app/ai/prompts/product_results.py` 六段。

知识库：`KNOWLEDGE_GROUNDED_SYSTEM_PROMPT`。

辅助生成：标题、推荐问题、文件分析、文件内容增强。

### 继续由代码控制

段落顺序、启用条件、section identity；日期与相对日期锚点的计算；`package_id` 枚举、canonical tool order、每包工具映射；结构化输出 Schema 与 `response_format`；工具权限、能力包定义、路由枚举；字面层分类规则；Run 冻结骨架与所有安全校验。

Skill 正文单独说明：`app/ai/skills/registry.py:23-31` 的 `_PACKAGE_SKILLS` 对 `verified-research 1.0.0` 写死 `content_sha256`。SKILL.md 带 `allowed-tools`，属权限面，**不进入 PromptHub**；英文化时按 semver 升到 `1.1.0` 并同步更新 pin，否则 `skill_load_failed` 会让 `verified_web` 包静默降级。

### 结构化契约值的机器校验

分类器 prompt 正文内联了 15 个 `package_id`、canonical tool order 与每包工具映射，真值在 `_MODEL_PACKAGE_IDS`（`run_capability_model_classifier.py:23`）、`_CANONICAL_TOOL_ORDER`（`:44`）与 `CAPABILITY_PACKAGE_EXTERNAL_TOOL_NAMES`。正文可热更后，写错一个包名会使 `_parse_model_route` 返回 `None` → `_fail_closed("invalid_response")` → 全量降级为 `clarification_only`，而 bundle 校验完全通过。

**定稿结论：taxonomy 由代码真值渲染为受控变量注入。**

- `package_id` 枚举、canonical tool order、包到工具映射一律由代码真值渲染成受控变量，注入分类器 prompt。
- PromptHub 只管理通用语义边界那部分正文。
- 发布校验只验证「必需变量齐备」与「渲染结果合法」，**不从可编辑自然语言正文反向解析枚举**。

不采用「在 bundle 校验中反向解析正文枚举」的方案：它会把标点、示例措辞和英文化表达变成协议解析器的输入，既脆弱，又重新制造了本规格 P1 要消除的正文耦合。

### 变量与转义约定

`prompt_bundle.py:295-311` 用 `string.Formatter` 解析并执行 `content.format(...)`，要求占位符集合与 `variables` 完全相等。含 JSON 样例或裸 `{` / `}` 的正文会被解析成字段名或抛 `ValueError`，导致整包拒绝。

规格要求：定死转义约定（统一 `{{` / `}}`），并为每个新增 spec 补一条「代码默认值自身能通过 bundle 校验」的往返测试。

### 快照必须先于能力分类解析

当前调用顺序使「分类器读取 Run 启动时已冻结的正文」在字面上无法成立。已核对的实际时序：

1. `build_agent_loop_lifecycle_call()`（`agent_loop_wiring.py:325`）先调用 `build_agent_loop_call_config_from_inputs()`；
2. 其中 `build_agent_loop_call_config()` 在 `agent_loop_request_prep.py:278` 执行 `resolve_run_capability_route()`，**能力分类在此完成**；
3. 之后才 `assemble_agent_loop_lifecycle_call()` → lifecycle `_start_run()`（`agent_loop_lifecycle.py:191`）；
4. 最后 `prepare_messages_fn()`（`:340`）组装消息并生成 `prompt_snapshot`。

即分类发生在 Run 启动**之前**，此时并不存在「已冻结的正文」。若分类器与后续组装各自解析，同一个 Run 的分类依据与执行正文可能来自不同 revision。

**定稿结论：**

- 在**能力分类之前**一次性解析出完整的不可变 `PromptBundleSnapshot`。
- 同一个快照对象同时传给分类器与后续消息组装，全程按引用传递，不重新解析。
- Run 启动时持久化该快照的 revision 与 fingerprint。
- 分类器**只读内存快照**，在 1.5 秒预算（`_HARD_TIMEOUT_SECONDS`）内不访问数据库——`resolve_prompt_template` 缓存未命中时会做同步 DB 读（`prompt_bundle.py:266` `SessionLocal`），该路径已有 `_can_begin_blocking_work` 门控，说明它对阻塞高度敏感。

这样分类结果与执行正文才真正来自同一 revision。快照的解析点因此上移到 `build_agent_loop_call_config_from_inputs()` 之前，成为 Run attempt 冻结的实际起点。

## 剩余范围归类（已定稿）

以下三组此前标为待定，现按复审结论确定唯一归类，实施阶段不得再行裁量：

| 条目 | 位置 | 归类 | 处理 |
|---|---|---|---|
| AMap / FlyAI 工具 description | `amap_product_tools.py:137` / `:168` / `:245`；`flyai_travel_tools.py:225` / `:257` | **结构化工具契约，继续代码控制** | 因「全局指令统一英文」覆盖所有模型可见指令，故在 P5 一并英文化，但**不进入 PromptHub** |
| Kimi 话题策划 system prompt | `kimi_search_service.py:16` | **独立辅助调用，代码控制** | P5 英文化；记录自己的 prompt revision，**不并入主 Run bundle** |
| 文件处理 system 消息 | `file_processor.py:287` | **独立辅助调用，代码控制** | 同上 |
| FlyAI 事实边界 system prompt | `flyai_travel_tools.py:47` | **可变的模型行为正文** | 从 MCP 模块拆出，归入产品结果事实边界 catalog，进入 PromptHub |

判据是**语义角色而非代码载体**：工具 description 与入参 schema 语义耦合，属契约面；事实边界约束的是模型输出行为，属可热更新正文，其载体恰好在 MCP 模块不改变归类。

独立辅助调用记录自己的 prompt revision，不伪装成沿用主 Run 快照——与 Run attempt 冻结语义一节中对标题、推荐问题的要求一致。

## 迁移与发布顺序

1. **P0 声明即消费。** catalog 单一事实源 + 启动期 fail-fast + 契约测试；`11` 改为派生。**apply 前必须通过字节一致前置校验或已发布代码默认值基线 bundle**，满足后行为零变化。
2. **P1 稳定 section identity。** 输入侧身份改为 sidecar metadata 并在送 provider 前剥离，去重/清理/语言策略按身份工作；输出侧 reasoning 净化作为独立 best-effort 防线单独立项与验收。输入侧行为零变化。
3. **P2 单 Run attempt 冻结。** 快照解析点上移到能力分类之前，同一不可变快照贯穿分类与组装；Run 启动持久化 revision 与 fingerprint；新 Run 独立解析；`extra_system_prompts` 改传 section id。
4. **P3 完整性与可回滚。** canonical checksum、`source_revision` 分离、`catalog_version`、受审计 rollback hold、独立冲突诊断状态、修正 active-LKG diff 基线。
5. **P4 多 worker 最终一致。** interval 60 秒、TTL 30 秒、保留各 worker 轮询与数据库互斥、120 秒收敛、150 秒告警。
6. **P5 扩 catalog、英文改写、联网边界修正。** 按分批顺序：辅助生成任务 → 搜索上下文与工具 description → 收尾与续写 → 计划与深度研究 → 分类器最后。英文化与 marker 切换必须与 P1 同批或在其之后。SKILL.md 升版本并更新 pin。

P5 内部先在 `shadow` 模式校验字节、变量、完整性与差异，再在 dev 切 `apply` 确认同步、LKG、Run 冻结、审计与回滚，最后经代码门禁与真实模型盲测方可部署生产。

## 验收

### 自动化契约

- bundle 缺项、重复 slug、未知 slug、非法变量、必需变量缺失、正文为空、`source_revision` 重算不相等时整包拒绝，原 LKG 不变。
- **发布门禁不包含任何对本地化正文子串的校验。** 身份由 bundle key / slug 与内部 sidecar section identity 承担，正文中不存在必须出现的 marker；「缺少 catalog 项」已由「bundle 缺项」覆盖。保留 marker 校验会在英文化后重新把发布流程绑死在中文措辞上。
- 同一 `source_revision` 对应不同重算结果时拒绝并产生独立 `revision_conflict` 诊断，LKG 不变，且不覆盖本地行。
- P0 apply 前置校验：待激活 bundle 与代码常量字节一致（或已发布代码默认值基线 bundle），不满足则 fail closed 且不进入 apply。
- hold 状态跨进程重启、跨 worker 保持一致；held 期间 scheduler 只 shadow-fetch 与告警，绝不 activate。
- PromptHub timeout / 401 / 5xx / 坏响应不影响聊天可用性。
- PromptHub 全程 5xx 期间仍可执行本地回滚，且 hold 生效后后续同步不会冲掉回滚结果。
- 解除 hold 后恢复跟随 published revision。
- 同一 Run attempt 在后台切换 bundle 前后保持原 revision、正文与 fingerprint；新 Run 使用新 revision 并保留 `previous_run_id`。
- 输入侧（确定性）：正文被替换后 `inject_*` 仍不重复注入，收尾清理仍能剔除控制契约，语言契约不叠加。
- 输出侧（best-effort）：用户可见 reasoning 净化单独定义漏删/误删的失败语义与验收，不并入上一条，也不承诺由 section identity 保证。
- 聊天热路径无 PromptHub HTTP 请求。
- catalog 每一项都能被真实注入路径消费；缺少 resolver 时启动失败。
- 代码默认值、PromptHub 条目与 catalog 均为英文；动态用户内容与外部原文不受此限。
- 工具权限、能力路由结构化 Schema、Skill 终态、Trajectory 投影与现有 Agent 行为契约不得回退。

### 收敛验收

多 worker 环境实测「发布 → 所有 worker 创建的新 Run 都使用新 revision」的实际耗时，健康状态下应在 120 秒内收敛；超过 150 秒触发告警。结果写入 runbook。

### 自然盲测

使用真实用户表达，不在消息中指定工具、指定路由或要求预设答案；不使用「请只回复……」「请联网搜索……」一类诱导样本；不为「深圳和杭州」增加关键词、正例或路由分支。

覆盖：需要当前外部事实的隐式请求、显式最新请求、稳定知识、创意 / 翻译 / 计算、禁用联网、工具不可用、中文与英文同义请求。

每个样本必须分开记录：

- **决策层**：`literal` / `model` / `failed`。`_classify_literal_layer`（`app/services/stream/run_capability_router.py:799`）在模型分类器之前运行，被字面层截住的样本改 prompt 不产生任何效果；分类器已按此打点（`_log_result`）。
- **分类是否正确**。
- **工具是否真实执行**：以 tool_call / tool result 事件为准，不以回答文字判定。
- **证据质量**：引用编号是否确实来自本轮研究证据工作集。
- **失败归因分布**：`credentials_missing` / `input_budget_exceeded` / `deadline_exceeded` / `invalid_response` 均返回 `clarification_only`。英文 prompt 变长会同时抬高输入 token 与首 token 延迟，可能把原本 `model` 分类的样本推成 `deadline_exceeded`，表现为「联网判断变差」但根因是预算。需记录改动前后的 `duration_ms` 与 `error_type` 分布，并为英文 prompt 设 token 预算上限（当前 `_HARD_MAX_INPUT_TOKENS = 2000`）。
- **实际 bundle revision**。

使用至少两个支持工具调用的模型做 dev 盲测，报告改动前后的分类别对比与失败样本。

代码门禁通过不等于模型效果验收通过。

## 非目标

- 不建设通用 Prompt 在线编辑器、A/B 实验平台或秒级推送通道。
- 不引入 leader-only 轮询或跨进程缓存失效通知。
- 不按模型维护多套 provider 专用 Prompt；只有盲测出现稳定可复现差异时才另行立项。
- 不允许管理员通过 Prompt 正文新增工具、扩大权限或改变服务端冻结骨架。
- 不以修改盲测期望值、加入单句特判或诱导模型回复的方式提高通过率。

## 仍需运行时验证的假设

设计通过复审不代表以下假设成立，实施与验收阶段必须逐条取证：

1. PromptHub 的 published bundle 在真实运行中不会复用 `source_revision`（算法本身已在 `HyxiaoGe/prompthub@5c9456e` 核实为内容摘要，此处只验证运行期不出现同摘要不同内容）。真实 `variables` 的实际存储形态（字符串列表或对象列表）需在 dev 抓取一次真实响应确认，以验证重算路径。
2. 生产实际 worker 数、scheduler 是否确为每进程一份、`pg_advisory_xact_lock` 在真实 PostgreSQL 上的争用表现。
3. 英文 system prompt 的 token 增量是否会突破分类器 `_HARD_MAX_INPUT_TOKENS = 2000` 与 1.5 秒超时预算。
4. section identity 改造后，中文对话中模型 reasoning 的实际措辞是否仍能被泄漏拦截覆盖。此项只能实测，不能由单测证明。
5. 至少两个支持 function calling 的模型上，英文 prompt 的分类、工具真实执行与证据质量不劣于现状。此项只能由盲测证明，不能由代码门禁证明。

## 当前状态

本文为设计规格，尚未实施。截至提交时未修改任何业务代码、未扩 catalog、未改动 Prompt 正文。规格复审通过后按 P0 → P5 顺序实施，每阶段单独提交与验收。

### 修订记录

- **v2（本次）**：吸收 PR #35 的 8 条 P1 复审意见。新增 P0 字节一致前置条件；拆分输入侧 section identity 与输出侧 reasoning 净化并定死身份载体；以 `HyxiaoGe/prompthub@5c9456e` 的 canonical contract 取代 revision 的二选一表述，并补充重算需保留原始有序 `variables` 的实现前提；补齐 rollback hold 的持久状态机契约；taxonomy 注入方式定稿为代码渲染受控变量；快照解析点上移到能力分类之前；三组剩余范围完成唯一归类；删除发布门禁中对本地化正文子串的 marker 校验。
- **v1**：三方复审共识初稿。
