# 动态工具发现原型交接报告

日期：2026-09-22，Asia/Shanghai。首轮交付，交 Codex 审查。未推送、未开 PR、未部署。

## 1. 工作位置

| 项 | 值 |
|---|---|
| 工作树 | `/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922` |
| 分支 | `cursor/dynamic-tool-discovery-c223` |
| remote | `HyxiaoGe/fusion` |
| base | `4c185cfca843e804143eaa5e16e2af8d804c1329` |
| head | 本分支 `cursor/dynamic-tool-discovery-c223` 最新提交（未推送） |
| 实际 base | 与任务书核对的 `master` 一致，未前移 |

未提交修改清单见文末；提交后以 `git status` 为准。未改旧 issue 工作树、`fusion-api`/`fusion-ui`、研究快照。

## 2. 修改文件与调用链

新增：

- `backend/app/services/stream/dynamic_tool_discovery.py`：授权目录、`tool_search`、promote、未加载/未授权回执。
- `backend/app/services/stream/dynamic_tool_discovery_fixtures.py`：假工具（天气/车次/搜索/读取/`mcp_readonly_probe`），合成结果标明合成。
- `backend/scripts/dynamic_tool_discovery_compare.py`：offline / dry-run / live。
- `backend/test/services/stream/test_dynamic_tool_discovery.py`：P01–P12。

窄接入：

- `agent_loop_request_prep.py`：`options["dynamic_tool_discovery"] is True` 时跳过 `resolve_run_capability_route` / `classify_fn`，初始只公告 `tool_search`（及计划开启时的 `update_plan`）。
- `agent_loop_execution.py` / `runtime` / `state`：挂载同一次 Run 的 session；计划允许集初始不含未加载产品工具。
- `agent_loop_driver.py`：计划模式仍开放 `tool_search`。
- `plan_control.py`：计划未成立时允许执行 `tool_search`，其它外部工具仍 `plan_required`。
- `tool_round.py`：未公告调用区分 `tool_authorized_but_not_loaded` 与 `tool_not_authorized`。
- `limit_summary_fact_guard.py`：`requires_catalog_evidence` 承接外部事实义务，不静默关守卫。
- `agent_loop_lifecycle.py`：发现路径不把非法假 package 写入 `TrajectoryCapabilityResolution`。
- `run_capability_router.py`：resolution 增加 `requires_catalog_evidence` 字段（默认 False，老路径不变）。

实际调用链（候选）：

`build_agent_loop_call_config`（opt-in，不调分类器）→ `prepare_agent_loop_messages`（目录 prompt）→ `build_agent_loop_execution` / `run_agent_loop` → `run_agent_round`（可变 `call_kwargs.tools`）→ `handle_tool_calls_round` / `execute_tools_fn` → `tool_search.promote` 同步 schema、handler、binding、计划 enum/`allowed_tool_names`。

分类器未调用证据：`test_p01_*` 对 `resolve_run_capability_route` 打 patch 为 `AssertionError`，并对 `classify_fn` 同样断言；P12 关闭开关后分类器仍被调用。

## 3. P01–P12

命令：

```bash
cd /Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922/backend
/Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest test/services/stream/test_dynamic_tool_discovery.py -q --tb=line
```

退出码：`0`（13 passed）。日志：`/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922/backend/tmp/dynamic-tool-discovery/p01-p12-pytest.log`（gitignored）。

受影响回归（同解释器）：`test_agent_loop_request_prep.py`、`test_agent_loop_driver.py`、`test_agent_loop_execution.py`、`test_tool_round.py`、`test_network_budget.py`、`test_limit_summary_fact_guard.py`、`test_plan_coordinator.py`、`test_agent_loop_lifecycle.py` 合计 **395 passed**，退出码 0。

| 编号 | 结果 | 入口 | 说明 |
|---|---|---|---|
| P01 | 通过 | `test_p01_discover_weather_then_trains_without_classifier` | 脚本控制先后发现天气/车次并执行；分类器未调用 |
| P02 | 通过 | `test_p02_unloaded_authorized_call_is_intercepted_then_executable` | 未加载拦截后发现再执行 |
| P03 | 通过 | `test_p03_unauthorized_name_never_activated` | 不返回 schema、不激活、不执行 |
| P04 | 通过 | `test_p04_network_denial_blocks_discovery_and_execution` | 复用现有否定信号；`web_search` 执行计数 0 |
| P05 | 通过 | `test_p05_idempotent_discover_and_shared_budget` | 重复发现幂等；共享 `SharedFixtureBudget` 对象不重建；耗尽后隐藏/不执行第二产品工具 |
| P06 | 通过 | `test_p06_failure_stays_in_context_and_allows_alternative` | 失败进入 `failed_tool_names`，可发现 `web_search` |
| P07 | 通过 | `test_p07_plan_enum_and_allow_set_stay_aligned` | coordinator、update_plan enum、执行允许集一致 |
| P08 | 部分通过 | `test_p08_empty_error_and_url_without_body_are_not_evidence` | 空结果/错误/无正文不记有效证据；`resolve_no_evidence_answer` 拦住班次报价。**未覆盖**：脚本 `stop` 收口未自动走 `limit_summary` 最后交付；未在本路径证明 `product_answer_validator` 阻断（按任务不重写该文件） |
| P09 | 通过 | `test_p09_discovery_does_not_reset_deadline_or_start_new_calls_after_limit` | `run_start`/步数上限不因发现重置；触顶后无新天气执行 |
| P10 | 通过 | `test_p10_two_runs_do_not_leak_loaded_tools_or_budget` | session/预算对象隔离 |
| P11 | 通过 | `test_p11_product_events_and_failed_tool_does_not_emit_success_block` | 成功发 `WeatherResultsBlock`；失败不发成功产品块。只证明事件合同，非浏览器 |
| P12 | 通过 | `test_p12_default_path_still_classifies` | 未启用实验入口时分类与生命周期 DTO 路径不变（另测 `_run_config` 跳过假包） |

禁止用总 passed 数掩盖：P08 最后交付阻断未覆盖。

## 4. 对照脚本

```bash
python scripts/dynamic_tool_discovery_compare.py --mode dry-run
python scripts/dynamic_tool_discovery_compare.py --mode live   # 无 --max-requests → 退出 2
```

dry-run 退出码 0，日志：`.../tmp/dynamic-tool-discovery/dry-run.log`。打印 alias、6 用例、每臂 2 次、两路径、每 Run 上限、输出目录。live **未跑**；缺限额拒绝；无授权预算、消耗 0。offline 模式会再跑上述 pytest，本轮以直接 pytest 为准。

六类句子已写入脚本（问候、单一天气、天气加车次、失败替代、禁网、空结果要班次），未按正则造句。压力测试与正常对照在 `kind` 字段分开。

## 5. 被替代的旧包约束与未覆盖生产依赖

候选绕开：字面/模型包分类、包工具数量形状（最多 3+2）、包生成的最少调用次数、`mixed_itinerary` 假包。

承接：授权目录 + 当前已加载集合；计划允许集随 promote 扩展；事实义务用 `requires_catalog_evidence`；网络否定复用 `_resolve_network_scope`（覆盖仍受原正则限制，不在本轮重写）。

仍未覆盖：Skill 加载与精确相等约束、深度研究阶段机、续跑/历史 `capability_resolution` 快照、前端协议、真实 MCP/FlyAI/高德、`product_answer_validator` 全面改造。上述场景标记 `unsupported_scenes`，不伪装兼容。

## 6. 回退与限制

- 默认 `dynamic_tool_discovery` 关闭，产品路径不变。
- 回退：去掉 opt-in 或还原本分支相对 `4c185cfc` 的 diff。
- 不得声称已上线、已更快、已还清正则债务。
- 假工具结果含「合成测试」limitation；未调用付费产品工具。
- 内存 resolution 的 `package_id=dynamic_discovery` **不**写入轨迹 DTO。
- 自然语言授权规则未重写。

## 7. Git

已本地提交到 `cursor/dynamic-tool-discovery-c223`。未推送。
