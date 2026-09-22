# 动态工具发现 P08 最终交付保护

日期：2026-09-22，Asia/Shanghai。按 `cursor-handoff-p08-20260922/TASK.md` 实施。未推送、未开 PR、未部署、未调用真实模型/供应商。本轮模型调用 0。

## 1. Git

| 项 | 值 |
|---|---|
| 工作树 | `/Users/sean/code/fusion/.worktrees/dynamic-tool-discovery-20260922` |
| 分支 | `cursor/dynamic-tool-discovery-c223` |
| 起始 HEAD | `fbecf67346bd46248fd833377d275e8ba98263e3` |
| 最终 HEAD | 以工作树 `git rev-parse HEAD` 为准（本轮本地提交，未推送） |
| 详细边界清单 | `docs/implementation-plans/dynamic-tool-discovery-production-boundaries.md` |

未改 #107/#109。未改正则大面积重写、`product_answer_validator.py`、对照 HTTP 协议适配。

## 2. 改动文件与原因

| 文件 | 原因 |
|---|---|
| `backend/app/services/stream/agent_loop_driver.py` | 发现路径 `runtime.tool_discovery is not None` 时一律 `defer_output`，避免不安全正文先流后改 |
| `backend/app/services/stream/agent_loop_round_outcome.py` | 延期纯文本/网页恢复提交前走 `resolve_no_evidence_answer`；产品块存在但无可用事实时，即使 validator 放行也不把空块当证据 |
| `backend/test/services/stream/test_dynamic_tool_discovery.py` | ScriptedRounds 尊重 `defer_output`；P08 从真实 `run_agent_loop` → round_outcome → `append_chunk` → `persist_run_message` → `complete_agent_run` 取证 |
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

退出码 0，**29 passed**（含 P08 子项与既有 HTTP 协议测试）。

相关回归：`test_agent_loop_round_outcome.py` + `test_limit_summary_fact_guard.py` + `test_agent_loop_driver.py` + `test_product_answer_no_result.py` + `test_product_answer_validator.py`：**252 passed**。

ruff check/format 受影响文件通过。

| 条件 | 修复前实测 | 修复后实测 | 仍未验证 |
|---|---|---|---|
| 零工具直接写班次/报价/气温 | 旧 P08 不覆盖该路径；stop 且不延期时正文会进 content_blocks | `test_p08_zero_tools_fabricated_facts_are_not_streamed`：`defer_output=True`；`answering` 与落盘均为 `NO_EVIDENCE_ANSWER_TEXT`，无 `G7301`/`28 度`；`run_completed` 发出 | 未走真实 SSE/Redis；未用生产 `stream_round` |
| 已发现未执行却声称查到了 | 未覆盖 | `test_p08_discovered_but_not_executed_is_not_evidence`：`weather_forecast` 已 loaded、execute_count=0；交付无 28 度 | schema 可见 ≠ 证据，仅脚本反例 |
| 产品失败或空 items | 循环后手工 `resolve_no_evidence_answer` 对硬编码字符串为真，未断言流/落盘 | `test_p08_empty_error_and_url_without_body_are_not_evidence` 在 `PRODUCT_ANSWER_REPAIR_ENABLED` False/True 下：事件与保存均无 `G7301`/`73 元`；车次执行 1 次。`test_p08_failed_weather_does_not_deliver_forecast`：失败天气后无 28 度 | 空 `train_results` 成功块仍可能进入产品提交分支，靠 validator/无可用证据守卫，不是语义真假检测 |
| 搜索/读取仅 URL/标题、正文空 | 旧 P08 执行了空搜索/空读，但只验事后守卫 | 同上用例含 `web_search` empty 与 `url_read` url_empty；交付无班次价 | 未单独拆 URL 用例；recovery_evidence 由真实 tool_round 写入，测试未手塞已验证标记 |
| 高铁有效、天气缺 26 日；混写 | 未覆盖 | `test_p08_mixed_train_valid_weather_gap_keeps_train_only`：G7301 保留，28 度不出现。天气 fixture 只覆盖 9 月 22—25 日（合成） | 未证明所有混写自然语言都能拆开；只覆盖现有 grounded/validator 合同 |
| 触顶 limit_summary | 发现测试用 stub `_limit_summary`，不进事实守卫 | `test_p08_limit_summary_without_evidence_is_guarded`：真实 `run_limit_summary_step`，仅 stub llm/stream；总结 `defer_output` 仍为 True；无 28 度；`session_status=limit_reached`；天气未执行、run_start 未重置 | stream_round 为 stub，不是正式 token 消费器 |
| 有有效结果且忠实转述 | 未作为 P08 交付验 | `test_p08_faithful_weather_result_is_delivered`：22 日 24 度进入 answering 与落盘，不是无证据文案 | 忠实模型句可能不含 `SYNTHETIC_LIMITATION`；未强制改写附带合成声明 |
| 问候 / 用户提供数字 | 单元 `resolve_no_evidence_answer` + `requires_catalog_evidence=False` | `test_p08_greeting_and_user_numbers_are_not_blocked`：问候原样交付；“十点/十二点”保留 | 稳定百科知识未另造用例；只证明无动态事实数字与用户原文数字 |

`compare` 脚本仍不是 P08 全交付入口。

## 5. 明确限制

- 没有通用自然语言事实验证器。只承诺结构化工具证据 + 现有动态数值/产品守卫覆盖的句子。
- 发现路径 defer 影响所有启用 `tool_discovery` 的 stop 回合（含问候）：先提交再流，避免先发后删。
- 协议 `protocol_reasoning_buf` 仍保留在 round_result；延期时用户可见 `reasoning_buf` 为空，测试不安全事实未进入 thinking 块。
- 未做浏览器、聊天 HTTP/SSE、真实供应商、取消/刷新/恢复全链路。
- 剩余 16 次 SDK 额度未动。

## 6. 生产接入

见同目录 `dynamic-tool-discovery-production-boundaries.md`。本轮不迁移、不开灰度开关。
