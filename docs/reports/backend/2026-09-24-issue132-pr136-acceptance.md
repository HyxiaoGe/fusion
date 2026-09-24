# #132 PR #136：真实模型盲测与候选验收

时间：2026-09-24（Asia/Shanghai）。基线 `master@991babce1fa06a30208881708b668a8572e64c1d`；候选 `PR #136@5da571d43caa05a9cb4d9e3a2e3f406d9046a328`。

## 环境与方法

- **dev 容器实测**：同一运行容器、同一 LiteLLM 配置、原始 `blind_routing_probe.json` 的 47 条。基线从 `/app` 加载；候选在 `/tmp/issue132-pr136/backend` 加载。按样本奇偶交错两臂，独立 Python worker，不覆盖运行中的 `/app`。四个生产改动模块及 fixture 去 CRLF 后的内容哈希分别匹配指定 Git 提交；候选临时副本没有已删的 `run_capability_request_signals.py`。
- 逐条原始机器记录：[paired-blind.jsonl](2026-09-24-issue132-pr136-paired-blind.jsonl)。`error` 是模型分类 callback 或 Python 异常类型；`-` 表示未观察到分类错误。按 fixture 的 `acceptable_packages` 统计包命中，仅作为本轮诊断分数；`expected_layer` 不参与包命中计分。
- 该轮只运行分类与工具可用性解析，没有执行页面任务或真实工具。历史轮次分数不参与比较。

## 逐条结果

| ID | 基线 layer / package / include_current_date / error | 候选 layer / package / include_current_date / error | 包命中 |
| --- | --- | --- | --- |
| travel-01 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | OK / OK |
| travel-02 | model / mobility_route / false / - | model / mobility_route / true / - | OK / OK |
| travel-03 | model / flight / true / - | model / flight / true / - | OK / OK |
| travel-04 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | OK / OK |
| travel-05 | model / mobility_route / true / - | model / mobility_route / true / - | OK / OK |
| travel-06 | model / mobility_route / false / - | model / mobility_route / true / - | OK / OK |
| travel-07 | model / clarification_only / false / - | model / clarification_only / false / - | OK / OK |
| travel-08 | model / clarification_only / false / - | model / clarification_only / false / - | OK / OK |
| travel-09 | model / train / true / - | model / train / true / - | OK / OK |
| travel-10 | model / train / true / - | model / train / true / - | OK / OK |
| weather-01 | model / weather / true / - | model / weather / true / - | OK / OK |
| weather-02 | model / weather / true / - | model / weather / true / - | OK / OK |
| weather-03 | model / fresh_web / true / - | model / fresh_web / true / - | OK / OK |
| place-01 | model / place_discovery / false / - | model / place_discovery / false / - | OK / OK |
| place-02 | model / place_discovery / false / - | model / place_discovery / false / - | OK / OK |
| direct-01 | literal / direct / false / - | model / direct / false / - | OK / OK |
| direct-02 | model / direct / false / - | model / direct / false / - | OK / OK |
| direct-03 | literal / direct / false / - | model / direct / false / - | OK / OK |
| direct-04 | model / direct / false / - | model / direct / false / - | OK / OK |
| transform-01 | model / transform / false / - | model / transform / false / - | OK / OK |
| transform-02 | model / transform / false / - | model / transform / false / - | OK / OK |
| web-01 | model / fresh_web / true / - | model / fresh_web / true / - | OK / OK |
| web-02 | literal / url_read / false / - | model / url_read / false / - | OK / OK |
| web-03 | model / fresh_web / true / - | model / fresh_web / true / - | OK / OK |
| web-04 | model / fresh_web / true / - | model / fresh_web / true / - | OK / OK |
| abstract-01 | model / direct / false / - | model / direct / false / - | OK / OK |
| abstract-02 | model / direct / false / - | model / direct / false / - | OK / OK |
| abstract-03 | model / direct / false / - | model / direct / false / - | OK / OK |
| abstract-04 | model / direct / false / - | model / direct / false / - | OK / OK |
| abstract-05 | model / direct / false / - | model / direct / false / - | OK / OK |
| boundary-01 | model / direct / false / - | model / direct / false / - | OK / OK |
| boundary-02 | model / transform / false / - | model / transform / false / - | OK / OK |
| boundary-03 | model / direct / false / - | model / direct / false / - | OK / OK |
| identity-01 | model / direct / false / - | model / direct / false / - | OK / OK |
| identity-02 | model / direct / false / - | model / direct / false / - | OK / OK |
| identity-03 | model / direct / false / - | model / direct / false / - | OK / OK |
| identity-04 | model / direct / false / - | model / direct / false / - | OK / OK |
| identity-05 | model / direct / false / - | model / direct / false / - | OK / OK |
| mcp_alias-01 | model / clarification_only / false / - | model / clarification_only / false / - | MISS / MISS |
| mcp_alias-02 | model / clarification_only / false / - | model / clarification_only / false / - | MISS / MISS |
| mcp_alias-03 | literal / verified_web / true / - | model / clarification_only / false / - | OK / MISS |
| mcp_alias-04 | model / clarification_only / false / - | model / clarification_only / false / - | MISS / MISS |
| verify_verb-01 | model / direct / false / - | model / direct / false / - | OK / OK |
| verify_verb-02 | model / direct / false / - | model / direct / false / - | OK / OK |
| verify_verb-03 | literal / verified_web / true / - | model / verified_web / true / - | OK / OK |
| verify_verb-04 | literal / verified_web / true / - | model / verified_web / true / - | OK / OK |
| verify_verb-05 | model / verified_web / true / - | model / verified_web / true / - | OK / OK |

本轮包命中：基线 **44/47**，候选 **43/47**；两臂分类错误均为 **0**。

## 差异与立项

- `mcp_alias-03`：基线 `literal / verified_web / true`，候选 `model / clarification_only / false`。这是本轮唯一包命中变差项，见 [#137](https://github.com/HyxiaoGe/fusion/issues/137)。不能据一条单轮样本估计总体失败率。
- `mcp_alias-01/02/04`：两臂均 `model / clarification_only / false`，属于已存在的独立缺陷，见 [#138](https://github.com/HyxiaoGe/fusion/issues/138)。
- `travel-02/06`：两臂同为 `model / mobility_route`，候选 `include_current_date` 从 `false` 变为 `true`，与本 PR 日期判据删除后的取值一致。`direct-01/03`、`web-02`、`verify_verb-03/04` 由字面层转模型层，包保持不变。

## 代码阅读核对

- 候选 `agent_loop_request_prep.py` 在常规请求与动态发现上下文均按包/任务策略传 `evidence_policy`，未找到为 `verified_web_v1` 赋值的生产路径；保留的守卫分支不能据此算作已验收。`requires_catalog_evidence=False` 仍写死。证据策略问题归 [#127](https://github.com/HyxiaoGe/fusion/issues/127)。
- 候选调用 `_parse_model_route` 时将 `all_network_denied` 固定传 `False`；原网络否定解析层已删。这里仅是代码路径核对，本轮盲测不证明真实用户否定语句的页面效果。
- 候选请求准备会注入当前日期；上表中的 `resolution.include_current_date` 是能力解析对象字段，不等于最终提示词是否含日期。因此保留该列并单独看实际请求准备。

## CI 与页面

- PR head `5da571d4` 的 GitHub API validation 曾报三个失败，均为测试在无模型候选时仍期待字面选包。已给相关测试注入明确候选，保留原有降级、计划模式及搜索事件链断言；本机目标 21 passed。最终 CI 结果及页面 `agent_events` 待补。
