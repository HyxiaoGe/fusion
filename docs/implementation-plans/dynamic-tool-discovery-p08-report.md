# 动态工具发现 P08 最终交付保护

日期：2026-09-22，Asia/Shanghai。按 `cursor-handoff-p08-20260922/TASK.md` 实施。未推送、未开 PR、未部署、未调用真实模型/供应商。本轮模型调用 0。

## 1. Git

| 项 | 值 |
|---|---|
| 工作树 | `/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922` |
| 分支 | `cursor/dynamic-tool-discovery-c223` |
| 起始 HEAD | `fbecf67346bd46248fd833377d275e8ba98263e3` |
| 正文守卫 HEAD | `0e91846ef1bd5800e36f29337e03d95b043f9d43` |
| 最终 HEAD | 以工作树 `git rev-parse HEAD` 为准（本轮本地提交，未推送） |
| 详细边界清单 | `docs/implementation-plans/dynamic-tool-discovery-production-boundaries.md` |

未改 #107/#109。未改正则大面积重写、`product_answer_validator.py`、对照 HTTP 协议适配。

## 2. 改动文件与原因

| 文件 | 原因 |
|---|---|
| `backend/app/services/stream/agent_loop_driver.py` | 发现路径一律 `defer_output`，并关闭 `allow_deferred_reasoning_output` |
| `backend/app/services/stream/agent_loop_round_outcome.py` | 延期纯文本/网页恢复提交前走 `resolve_no_evidence_answer`；空产品块不当证据 |
| `backend/app/services/stream/agent_loop_step_requests.py`、`limit_summary.py` | 发现路径触顶总结不展示/不保存未校验思考 |
| `backend/test/services/stream/test_dynamic_tool_discovery.py` | 正式 `run_agent_round`/`stream_round`；`finalize_completed_run`+生产 `persist_message` 读回 |
| 本报告与生产边界清单 | 任务要求的交付文档 |

旧包路径：`tool_discovery is None` 时 defer 条件不变。发现路径两臂若共用 driver，都会延期输出再守卫。

## 3. 证据类型说明

- **代码阅读**：driver 原先在无产品块时不延期；`resolve_no_evidence_answer` 只挂在 `limit_summary`。
- **修复前实测**：`test_p08_empty_error_and_url_without_body_are_not_evidence` 在循环结束后手工调用守卫；`ScriptedRounds` 不设 `output_deferred`；`persist_message_fn` 为空操作。原型报告已标 P08 部分通过。
- **修复后实测**：下列集成测试。模型输出为脚本反例，不是真实模型质量评估。假工具、合成班次/天气均标 `synthetic`。
- **历史真实模型**：见 `dynamic-tool-live-20260922-explicit-date/REPORT.md`，本轮不重跑、不改旧证据。

## 4. 矩阵逐项

命令：

```bash
cd backend && DATABASE_URL='sqlite:///:memory:' \
  /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest \
  test/services/stream/test_dynamic_tool_discovery.py -q --tb=line
```

正文守卫轮：**29 passed**。reasoning 收尾后：**34 passed，2 subtests passed**。

相关回归：`test_agent_loop_round_outcome.py` + `test_limit_summary_fact_guard.py` + `test_agent_loop_driver.py` + `test_product_answer_no_result.py` + `test_product_answer_validator.py`：**252 passed**。

ruff check/format 受影响文件通过。

| 条件 | 修复前实测 | 修复后实测 | 仍未验证 |
|---|---|---|---|
| 零工具直接写班次/报价/气温 | 审查 `check_real_stream.py` 在 `0e91846e`：正式 stream 发出不安全 reasoning，正文被替换但 ThinkingBlock 仍待保存 | `test_p08_formal_stream_holds_unverified_reasoning` + 复跑 `check_real_stream.py`：`allow_deferred_reasoning_output=False`；无 reasoning chunk；正文为无证据文案；协议 reasoning 保留 | 未走真实 SSE/供应商 |
| 已发现未执行却声称查到了 | 未覆盖 | `test_p08_discovered_but_not_executed_is_not_evidence`：`weather_forecast` 已 loaded、execute_count=0；交付无 28 度 | schema 可见 ≠ 证据，仅脚本反例 |
| 产品失败或空 items | 循环后手工 `resolve_no_evidence_answer` 对硬编码字符串为真，未断言流/落盘 | `test_p08_empty_error_and_url_without_body_are_not_evidence` 在 `PRODUCT_ANSWER_REPAIR_ENABLED` False/True 下：事件与保存均无 `G7301`/`73 元`；车次执行 1 次。`test_p08_failed_weather_does_not_deliver_forecast`：失败天气后无 28 度 | 空 `train_results` 成功块仍可能进入产品提交分支，靠 validator/无可用证据守卫，不是语义真假检测 |
| 搜索/读取仅 URL/标题、正文空 | 旧 P08 执行了空搜索/空读，但只验事后守卫 | 同上用例含 `web_search` empty 与 `url_read` url_empty；交付无班次价 | 未单独拆 URL 用例；recovery_evidence 由真实 tool_round 写入，测试未手塞已验证标记 |
| 高铁有效、天气缺 26 日；混写 | 未覆盖 | `test_p08_mixed_train_valid_weather_gap_keeps_train_only`：G7301 保留，28 度不出现。天气 fixture 只覆盖 9 月 22—25 日（合成） | 未证明所有混写自然语言都能拆开；只覆盖现有 grounded/validator 合同 |
| 触顶 limit_summary | stub stream 把 reasoning 留空，漏检总结通道 | `test_p08_formal_stream_limit_summary_holds_reasoning`：正式 stream；无 reasoning 事件；保存为无证据文案 | 未走真实 SSE |
| 有有效结果且忠实转述 | 仅 ScriptedRounds | 保留原用例；`test_p08_formal_stream_tool_round_keeps_protocol_reasoning` 正式 stream 交付 24 度，无 28 度 | 忠实模型句可能不含 `SYNTHETIC_LIMITATION` |
| 问候 / 用户提供数字 | 单元守卫 | 保留原用例；`test_p08_formal_stream_greeting_is_not_blanked` 问候正文保留、思考不外发 | 稳定百科知识未另造用例 |

`compare` 脚本仍不是 P08 全交付入口。

## 5. 明确限制

- 没有通用自然语言事实验证器。只承诺结构化工具证据 + 现有动态数值/产品守卫覆盖的句子。
- 发现路径 defer 影响所有启用 `tool_discovery` 的 stop 回合（含问候）：先提交再流，避免先发后删。
- 协议 `protocol_reasoning_buf` 仍回填下一工具回合；发现路径不向用户展示或保存未校验思考。
- 未做浏览器、聊天 HTTP/SSE、真实供应商、取消/刷新/恢复全链路。
- 剩余 16 次 SDK 额度未动。

## 6. 生产接入

见同目录 `dynamic-tool-discovery-production-boundaries.md`。本轮不迁移、不开灰度开关。

## 7. Reasoning 通道收尾（审查 P1）

审查 HEAD `0e91846e`。Codex 离线反例 `cursor-review-p08-20260922/check_real_stream.py` 实测：正式 `run_agent_round` → `stream_round` 发出 reasoning「杭州明天最高 28 度，坐 G7301，二等座 73 元。」；正文被换成 `NO_EVIDENCE_ANSWER_TEXT`，ThinkingBlock 仍进入待保存块。当时 P08 测试把 `reasoning_buf` 写死为空，漏检。

**修复**：发现路径与 knowledge_grounded 同一参数链设 `allow_deferred_reasoning_output=False`。不删除协议 reasoning。非发现路径思考策略未改。

**修复后实测**（本地异步 delta，模型调用 0）：

| 证据 | 结果 |
|---|---|
| `check_real_stream.py` | `leaked_unsafe_reasoning=False`；driver 传 `defer_output=True, allow_deferred_reasoning_output=False`；无 reasoning chunk；保存为无证据文案；`protocol_reasoning` 仍含原句 |
| `test_p08_formal_stream_holds_unverified_reasoning` | 正式 stream；事件/可见/保存参数均无 G7301/28 度/73 元 |
| `test_p08_formal_stream_tool_round_keeps_protocol_reasoning` | 工具回合不发 reasoning；协议 reasoning 进入下一轮 `reasoning_content` |
| `test_p08_formal_stream_greeting_is_not_blanked` | 问候正文保留 |
| `test_p08_formal_stream_limit_summary_holds_reasoning` | 正式 stream 触顶总结无 reasoning 泄露 |
| `test_p08_finalize_completed_run_persists_safe_blocks` | **传给存储的参数**为无证据 TextBlock；**新 Session 读回**仅 `text`、无 thinking；`run_completed.finish_reason` 对齐 |

全文件 pytest 34 passed。仍未覆盖真实聊天 HTTP/SSE、Redis 恢复、供应商流、取消刷新、自然语言未被守卫覆盖的事实。
