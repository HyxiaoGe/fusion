# 动态工具发现原型交接报告

日期：2026-09-22，Asia/Shanghai。第五轮按实测报告修对照适配器工具参数协议与严格 HTTP 回归。未跑新 live 真模型、未推送、未开 PR、未部署。生产 `build_assistant_tool_message` 未改。

## 1. 工作位置

| 项 | 值 |
|---|---|
| 工作树 | `/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922` |
| 分支 | `cursor/dynamic-tool-discovery-c223` |
| remote | `HyxiaoGe/fusion` |
| base | `4c185cfca843e804143eaa5e16e2af8d804c1329` |
| 上一轮修复 HEAD | `6c92c97c1e53810cf856611277ff6bc4a03077c1` |
| 修复后 HEAD | 见文末 Git 节（本轮本地提交，未推送） |
| 实际 base | 与任务书核对的 `master` 一致，未前移 |

未改旧 issue 工作树、`fusion-api`/`fusion-ui`、研究快照。未改 #107/#109。

## 2. 修改文件与调用链

保留首轮原型，按审查 R1–R4 与交接口径修补。

核心：

- `dynamic_tool_discovery.py`：目录条目 `network_kind`；全局禁网覆盖产品查询与未知网络 MCP；字面 casefold 匹配；`DiscoveryExperimentContext`；opt-in 遇到 Skill/deep_research/continuation 抛 `DynamicToolDiscoveryUnsupportedError`。
- `dynamic_tool_discovery_fixtures.py`：固定实验时钟 `2026-09-22T09:00+08:00`；增加测试用网络 MCP `mcp_network_probe`；本地只读 `mcp_readonly_probe`。
- `agent_loop_request_prep.py`：不再创建 `package_id="dynamic_discovery"`；`capability_resolution=None`，必要字段放实验上下文。
- `agent_loop_driver.py`：发现路径计划首轮 `tool_choice=required`，不强制 `update_plan`。
- `plan_control.py` / `agent_loop_execution.py` / `tool_round.py`：仅当本 Run 启用发现控制工具时跳过 `plan_item_required`；旧路径不按名字全局放宽。
- `agent_loop_lifecycle.py`：轨迹配置写实验上下文，不写假 package DTO。
- `agent_loop_wiring.py`：传入 `previous_run_id` 以便续跑拒绝。
- `limit_summary_fact_guard.py`：目录可用性不再自动变成问候证据义务。
- `scripts/dynamic_tool_discovery_compare.py`：两臂配对执行器、假传输层、预算中止、请求 hash、dry-run 来自真实配置。
- `test_dynamic_tool_discovery.py`：补 R1–R4、P09 改写、不支持场景真拒绝、问候保护、脚本遵守 `tool_choice`。

调用链不变：opt-in `build_agent_loop_call_config`（不调分类器）→ `run_agent_loop` → `handle_tool_calls_round` → `tool_search.promote`。

## 3. P01–P12

命令：

```bash
cd /Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922/backend
DATABASE_URL='sqlite:///:memory:' /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest test/services/stream/test_dynamic_tool_discovery.py -q --tb=line
```

退出码：`0`（**22 passed**）。日志：`backend/tmp/dynamic-tool-discovery/p01-p12-pytest.log`（gitignored）。新增 `test_compare_http_protocol_keeps_string_tool_arguments`。

相关回归（同解释器，DATABASE_URL 内存 SQLite）：

- `test_dynamic_tool_discovery.py` + `test_agent_loop_request_prep.py` + `test_agent_loop_driver.py` + `test_plan_control.py` + `test_limit_summary_fact_guard.py` + `test_agent_loop_lifecycle.py`：**282 passed**，退出码 0。
- `test_tool_round.py` + `test_agent_loop_execution.py` + `test_network_budget.py` + `test_plan_coordinator.py`：**148 passed**，退出码 0。

| 编号 | 结果 | 入口 | 说明 |
|---|---|---|---|
| P01 | 通过 | `test_p01_discover_weather_then_trains_without_classifier` | 分类器未调用 |
| P02 | 通过 | `test_p02_unloaded_authorized_call_is_intercepted_then_executable` | 未加载拦截后发现再执行 |
| P03 | 通过 | `test_p03_unauthorized_name_never_activated` | 不返回 schema、不激活、不执行 |
| P04 | 通过 | `test_p04_network_denial_blocks_discovery_and_execution` | 全局禁网覆盖天气/车次/搜索/读取/网络 MCP；本地只读 MCP 可执行 |
| P05 | 通过 | `test_p05_idempotent_discover_and_shared_budget` | 幂等；共享预算不重建 |
| P06 | 通过 | `test_p06_failure_stays_in_context_and_allows_alternative` | 失败可发现替代工具 |
| P07 | 通过 | `test_p07_plan_enum_and_allow_set_stay_aligned` 与 `test_plan_mode_tool_search_usable_before_and_after_valid_plan` | 计划前后均可发现；产品执行仍需计划绑定 |
| P08 | 部分通过 | `test_p08_empty_error_and_url_without_body_are_not_evidence` | 证据账本拦截合成班次。**未覆盖**：最后交付/`product_answer_validator` 阻断（按任务不重写该文件） |
| P09 | 通过 | `test_p09_discovery_then_limit_does_not_start_new_product_calls` | 先发现再 `max_steps=1` 触顶，天气未执行；不重置 `run_start` |
| P10 | 通过 | `test_p10_two_runs_do_not_leak_loaded_tools_or_budget` | Run 隔离 |
| P11 | 通过 | `test_p11_product_events_and_failed_tool_does_not_emit_success_block` | 事件合同 |
| P12 | 通过 | `test_p12_default_path_still_classifies` | 默认路径仍分类 |

禁止用总 passed 数掩盖：P08 最后交付阻断仍未覆盖。

## 4. 对照脚本

```bash
python scripts/dynamic_tool_discovery_compare.py --mode dry-run --output-dir tmp/dynamic-tool-discovery
python scripts/dynamic_tool_discovery_compare.py --mode live   # 无 --max-requests → 退出 2
python scripts/dynamic_tool_discovery_compare.py --mode live --max-requests 8 --transport fake --repeats 1 --output-dir tmp/dynamic-tool-discovery/fake-live
python scripts/dynamic_tool_discovery_compare.py --mode live --max-requests 8 --transport litellm
# 适配已实现；未设 FUSION_COMPARE_ALLOW_REAL_LLM 时真实 acompletion 发送次数为 0
```

| 模式 | 退出码 | 证据 |
|---|---|---|
| dry-run | 0 | 打印来自 `build_compare_config`，`max_tokens=4096`，固定日期 2026-09-22 |
| live 缺限额 | 2 | 拒绝运行，发送函数不调用 |
| live + fake 传输层 | 0 | 两臂走 `build_agent_loop_call_config` + `run_agent_loop` + `handle_tool_calls_round`；假工具 fixture |
| live + litellm（未授权真实发送） | 3 | `LiteLLMProxyTransport` 已实现；无 `FUSION_COMPARE_ALLOW_REAL_LLM=1` 时 `send_fn`/`acompletion` 次数为 0 |
| 真实 live | 未跑 | 消耗 0 |

实际两臂调用链：

`PairingExecutor._run_arm` → `build_agent_loop_call_config`（基线：生产 hybrid；live 分类走真实 `litellm.completion` 边界并先扣预算；fake 才用离线 fixture；候选不调分类）→ `prepare_agent_loop_messages` → `run_agent_loop` → 主模型传输。

R4 离线验收入口：`test_compare_pairing_uses_fusion_loop`、`test_compare_r4_messages_hybrid_usage_and_proxy`、`test_compare_r4_classifier_sdk_boundary_and_unevaluated_business`。

## 5. 逐条回复审查项

### R1 全局禁网

`denied_network_tool_names` 按条目 `network_kind` 处理：`all_denied` 拒绝 search/url/product_query/unknown_network。天气、车次、web_search、url_read、`mcp_network_probe` 不得加载或执行；`mcp_readonly_probe` 仍可。Codex `reproduce.py` `all_network_denied`：`weather_executions=0`，`train_executions=0`。

仅禁搜索 / 仅禁 URL 的语义仍由原 `_resolve_network_scope` 的 `web_denied`/`url_denied` 保留。未知 MCP 失败关闭。

### R2 计划模式发现入口

发现路径初次请求 `tool_choice=required`（可见 `tool_search` 与 `update_plan`），不再强制 `update_plan`。脚本若返回被 `tool_choice` 禁止的调用会失败。有效计划后，本 Run 的发现控制工具不要求 `_plan_item_id`；产品工具仍走原计划绑定。`reproduce.py`：`plan_first_request.tool_choice=required`；`discover_after_valid_plan` 的 `external_tool_calls` 含 `tool_search`。

### R3 目录查询

模型 query 只做 casefold 子串/词项匹配；正则元字符当普通文本。无匹配则 list/page；`select:` 仍可达。`reproduce.py` `untrusted_catalog_regex` 结果 `returned`（1 秒内）。

### R4 配对执行器

第三轮审查（`31a863f6`）确认循环已接入，但对照仍不具备有效取数条件。本轮只修实验链路，不改发现机制。

#### R4-A 正式消息与工具协议

- 两臂调用 `prepare_agent_loop_messages`：隔离 `file_repo` / 用户系统提示 / `build_llm_messages`，传入用例原文；用实验时钟渲染 `current_date`，实际发送内容含 `2026-09-22`。
- 发送序列化走 `PromptMessage.to_provider_messages()`：保留 assistant `tool_calls` 与 tool `tool_call_id`；`section_id` 只留在旁路 `first_provider_payloads.section_ids`。
- Fake 在首轮 messages 缺少用户原文时不再按 `case_id` 伪造工具调用。
- 请求 hash 基于实际发送 payload（messages/tools/tool_choice/max_tokens/fixed_date），不含 arm/repeat，不含凭据。

脱敏样例（天气基线首轮，不含密钥）：

- roles：`system, system, system, system, user`
- user：`帮我看看杭州这周末天气怎么样`
- 发送体含日期 `2026-09-22`，不含 `section_id`
- 旁路 section：`app_identity`, `tool_failure_policy`, `tool_usage_contract`, `current_date`
- 后续 tool `tool_call_id=call_weather`，assistant 带 `tool_calls`
- 基线本轮可见工具：`web_search`, `url_read`, `weather_forecast`

#### R4-B 基线 hybrid

- 基线 `classify_fn` 为生产 `classify_capability_request_with_model`，不重写分类器。
- 字面短路不发送。语义层 live 调用真实 `litellm.completion`（测试只 mock SDK），usage 来自响应；假 weather 回复只存在离线 fixture / `classify_completion_fn`。
- 高铁用例 SDK 返回 `train` 时基线可见工具含 `search_trains`、不含 `weather_forecast`；改回 weather 包后工具集随之变化。
- 无预算或无分类凭据时分类与主模型发送均为 0。live 不注入假凭据。候选分类发送为 0。

#### R4-C LiteLLM Proxy 解析

- `LiteLLMProxyTransport` 经 `LLMManager.resolve_model` 组包：`model=litellm_proxy/{alias}`，带 `api_base`/`api_key`，`num_retries=0`。
- 凭据只进发送参数，不进 `send_calls` 日志与 hash。
- 测试 mock 真正的 `litellm.acompletion`，解析 SDK `choices/message/tool_calls/usage`。未授权真实发送时发送次数为 0。

#### R4-D 完成判定与用量

- 机制状态与业务评价分开：`execution_ok` / `handler_counts` / 是否出现 `day_weather` 只作机械记录。
- 自然语言不能可靠判定时 `task_outcome=unevaluated`（待人工评估），`task_completed=False`。不把调用次数、失败结果或虚构天气标成完成或诚实。无正则评审器。
- 传输失败、预算耗尽、无正文不能标业务成功。
- 响应 usage 累加；任一侧缺失即 `usage_unknown`。`base` 固定为比较基线 `4c185cfc…`，不是本地 `master` ref。`response_cache_status=未知`；目录缓存另列 `catalog_cache_status`。

测试另含 `test_compare_r4_classifier_sdk_boundary_and_unevaluated_business`。真模型未执行，消耗 0。P08 最后交付缺口仍单列。

### 假 package

已删除 `package_id="dynamic_discovery"`。发现路径 `capability_resolution is None`，轨迹 `_run_config` 只写 `DiscoveryExperimentContext`。测试 `test_discovery_run_config_uses_experiment_context_not_fake_package`。

### P09

改为发现发生后再用 `max_steps=1` 触顶，证明发现后不再启动产品调用。

### 不支持场景

opt-in 遇到 Skill pin / `task_mode=deep_research` / `previous_run_id` 或 `stream_mode=continuation` 时抛 `DynamicToolDiscoveryUnsupportedError`，不静默回退分类路径。`test_unsupported_scenes_refuse_instead_of_silent_fallback`。

### `requires_catalog_evidence`

默认 False。注明 conservative experiment adapter。`test_greeting_does_not_require_catalog_evidence`：目录有天气工具时普通问候不被当成缺证据。

## 6. 被替代的旧包约束与未覆盖生产依赖

候选绕开：字面/模型包分类、包工具数量形状、包生成的最少调用次数。不伪造能力包。

承接：授权目录 + 已加载集合；计划允许集随 promote；网络否定复用 `_resolve_network_scope`；事实守卫对无证据动态事实仍拦截，问候不因目录在场而强制外部证据。

仍未覆盖：Skill/深度研究/续跑（现为明确拒绝）、前端协议、真实供应商、`product_answer_validator` 全面改造、P08 最后交付阻断。

## 7. 回退与限制

- 默认开关关闭。
- 回退：去掉 opt-in 或还原相对 `4c185cfc` 的 diff。
- 不得声称已上线、已更快、已还清正则债务。
- 假工具标明合成；固定实验日期，不用墙上时钟冒充固定数据。
- 自然语言授权规则未重写。

## 8. 审查复现

```bash
cd /Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922/backend
DATABASE_URL='sqlite:///:memory:' /Users/sean/code/fusion/fusion-api/.venv/bin/python /Users/sean/code/fusion/cursor-review-20260922/reproduce.py
```

本轮退出码 0。R1 天气/车次执行 0；R2 `tool_choice=required` 且计划后 `tool_search` 可执行；R3 正则查询返回而非超时。

## 9. Git

见文末本轮 Git 节。未推送。HEAD 以工作树 `git rev-parse HEAD` 为准。

## 10. 实测协议修复（2026-09-22）

依据 `/Users/sean/code/fusion/dynamic-tool-live-20260922/REPORT.md` 与 `CURSOR_FIX_PROMPT.md`。12 Run / 26 次 SDK 尝试已结束，本轮不追加采样、不跑真模型。

### 工具历史合同

`LiteLLMProxyTransport._parse_sdk_completion` 不再把 `function.arguments` `json.loads` 成 dict。协议层保持 JSON 字符串；handler 执行才用 `_handler_tool_arguments` 解析。非法 JSON 抛 `invalid_tool_arguments`，不改造成 `_raw`。Fake 发射同样写字符串。生产 `tool_round.build_assistant_tool_message` 仍原样回填，因此适配器必须交字符串。

两臂 `options["use_reasoning"]=True`，走现有 `protocol_reasoning_buf` / `should_use_reasoning`。真实天气响应里的 `reasoning_content` 会写回后续 assistant 历史。未另做思考通道。

### 严格 HTTP 回归

`test_compare_http_protocol_keeps_string_tool_arguments` 对本机 HTTP 替身跑正式 `litellm.acompletion`（不 mock 返回对象作为协议证明）：

1. 首轮返回录制形状：`arguments` 为 JSON 字符串，含 `reasoning_content`。
2. Fusion 循环执行假天气工具后，第二轮 HTTP JSON 中 `tool_calls[].function.arguments` 仍是字符串，可解析为原参数；`tool_call_id` 对齐。
3. 第二轮 assistant 历史含 `reasoning_content`。
4. 替身拒绝对象型 arguments（400）。修复后第二轮完成并给出最终回答。
5. 强制 HTTP 400、无 `choices` 的畸形 200：`execution_ok=False`，`task_outcome=error`，`final_output is None`（不用工具前导文本），`usage_unknown=True`，错误正文脱敏不含凭据。

此前 mock `acompletion` 对象测试仍保留，但不能覆盖此反例。

### 结果口径

- 失败请求无 usage：`record_usage(None, None)` → `usage_unknown=True`，不记零。
- `sdk_attempts` / `sdk_responses` 分开；异常不补 0 tokens。
- `mechanical_pair_complete` / `mechanical_incomplete_pairs` 看两臂 `execution_ok`。业务 `unevaluated` 仍进 `incomplete_pairs`，不能把待评估说成没跑。
- 禁网基线 direct 无日期、候选有日期：上下文合同差异，不作同上下文优劣。未改生产 direct 路由。
- 天气 fixture 覆盖 9 月 22—25 日，用户问 26—27 日：覆盖不足。完成一次调用 ≠ 查到周末。
- 线上 `NoneType ... choices` 与对象参数的精确因果仍未钉死。严格 HTTP 回放/本回归给出的是清晰 400。不把推断写成已证实。

未改正则、分类、动态发现产品逻辑、P08 最后交付守卫。未改 #107/#109。

相关回归：`test_tool_round.py` + `test_agent_loop_execution.py` **54 passed**。

## 11. Git

本轮审查对象 `6c92c97c`。已本地提交到 `cursor/dynamic-tool-discovery-c223`。未推送。HEAD 以工作树 `git rev-parse HEAD` 为准。
