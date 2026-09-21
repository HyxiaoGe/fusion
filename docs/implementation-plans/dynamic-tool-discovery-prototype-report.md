# 动态工具发现原型交接报告

日期：2026-09-22，Asia/Shanghai。Codex 审查 `b5eb6d94` 后的修复轮。未跑 live 真模型、未推送、未开 PR、未部署。

## 1. 工作位置

| 项 | 值 |
|---|---|
| 工作树 | `/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922` |
| 分支 | `cursor/dynamic-tool-discovery-c223` |
| remote | `HyxiaoGe/fusion` |
| base | `4c185cfca843e804143eaa5e16e2af8d804c1329` |
| 审查 HEAD | `b5eb6d94e327c0eb60f002fe0d20423d1c204a98` |
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

退出码：`0`（**19 passed**）。日志：`backend/tmp/dynamic-tool-discovery/p01-p12-pytest.log`（gitignored）。

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
python scripts/dynamic_tool_discovery_compare.py --mode live --max-requests 8 --transport litellm  # 本轮拒绝真模型，退出 3
```

| 模式 | 退出码 | 证据 |
|---|---|---|
| dry-run | 0 | `backend/tmp/dynamic-tool-discovery/dry-run.log`；打印来自 `build_compare_config`，`max_tokens=4096`，固定日期 2026-09-22 |
| live 缺限额 | 2 | 拒绝运行 |
| live + fake 传输层 | 0 | `backend/tmp/dynamic-tool-discovery/fake-live/`；两臂交错、基线分类计账、预算中止、请求 hash、完整输出写盘 |
| live + litellm | 3 | 本轮未授权真实模型 |
| 真实 live | 未跑 | 消耗 0 |

## 5. 逐条回复审查项

### R1 全局禁网

`denied_network_tool_names` 按条目 `network_kind` 处理：`all_denied` 拒绝 search/url/product_query/unknown_network。天气、车次、web_search、url_read、`mcp_network_probe` 不得加载或执行；`mcp_readonly_probe` 仍可。Codex `reproduce.py` `all_network_denied`：`weather_executions=0`，`train_executions=0`。

仅禁搜索 / 仅禁 URL 的语义仍由原 `_resolve_network_scope` 的 `web_denied`/`url_denied` 保留。未知 MCP 失败关闭。

### R2 计划模式发现入口

发现路径初次请求 `tool_choice=required`（可见 `tool_search` 与 `update_plan`），不再强制 `update_plan`。脚本若返回被 `tool_choice` 禁止的调用会失败。有效计划后，本 Run 的发现控制工具不要求 `_plan_item_id`；产品工具仍走原计划绑定。`reproduce.py`：`plan_first_request.tool_choice=required`；`discover_after_valid_plan` 的 `external_tool_calls` 含 `tool_search`。

### R3 目录查询

模型 query 只做 casefold 子串/词项匹配；正则元字符当普通文本。无匹配则 list/page；`select:` 仍可达。`reproduce.py` `untrusted_catalog_regex` 结果 `returned`（1 秒内）。

### R4 配对执行器

`PairingExecutor` + `FakeModelTransport` + `ExperimentBudget`。dry-run 打印实际配置。假传输层验证调度、预算中止、交错顺序、基线分类计账、固定日期、两臂写盘。本轮不连接真实模型。

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

已本地提交到 `cursor/dynamic-tool-discovery-c223`。未推送。HEAD 以工作树 `git rev-parse HEAD` 为准。
