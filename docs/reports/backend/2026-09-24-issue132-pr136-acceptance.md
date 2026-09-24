# #132 PR #136：真实模型盲测与候选验收

时间：2026-09-24（Asia/Shanghai）。基线 `master@991babce1fa06a30208881708b668a8572e64c1d`；盲测候选 `PR #136@5da571d43caa05a9cb4d9e3a2e3f406d9046a328`。后续测试修正提交为 `457831f5e2fee11a1bbe8365c9589ea5b7090735`，仅修改测试和本报告；页面候选镜像的生产源码与 `457831f5` 一致。

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

- PR head `5da571d4` 的 GitHub API validation 曾报三个失败，均为测试在无模型候选时仍期待字面选包。已给相关测试注入明确候选，保留原有降级、计划模式及搜索事件链断言；本机定向 37 passed，Ruff 检查与格式检查通过。`457831f5` 的 [CI 运行 35987705233](https://github.com/HyxiaoGe/fusion/actions/runs/35987705233) 中 API、UI、安全校验、required gate 均通过。完整本机后端测试本轮未重跑；CI 的 API validation 是独立证据。
- 页面基线和候选的每条 `agent_events` 均保留序号与类型索引：[master 144 条](2026-09-24-issue132-pr136-master-page-event-index.jsonl)、[候选 194 条](2026-09-24-issue132-pr136-candidate-page-event-index.jsonl)。索引包含所有事件，不以完成侧筛选；工具正文注入另从 `tool_call_logs.metadata.tool_observation` 核实。

## master 页面基线：现有登录态 Chrome

以下三条均在用户原有 `fusion.seanfield.org` Chrome 标签中新建会话发送，dev API 仍为 `master@991babce` 的已发布镜像；**这些不是 PR #136 候选页面验收**。每条都按 `conversation_id` 从数据库查询完整 `agent_events`，工具正文注入另核 `tool_call_logs.metadata.tool_observation`。页面文字、数据库事件与工具日志分层记录。

| 场景 | 页面观察 | 数据库完整事件流及工具日志 | 判定 |
| --- | --- | --- | --- |
| 普通问候「你好」 | [会话](https://fusion.seanfield.org/chat/4e203cd2-9397-4478-9d20-979b74bb7e9b)正常直答 | Run `448961302a4541eeb47ec52d2ef2d594`，14 条事件、序号 0–13 连续，`tool_call_started=0`，`run_completed` 在 11 | 通过 |
| 「请分别打开 https://example.com/ 和 https://www.example.com/，读取两页正文，分别用一句话概括各页内容；若某页未读到正文，请明确说明。」 | [会话](https://fusion.seanfield.org/chat/f07f965f-d487-445f-9422-0fd88e0bc614)回答原文称「两页都已读到正文」并分别概括 | Run `1052b9691b414e6782e35bc0ca2c63fb`，31 条事件、序号 0–30 连续；只有序号 11 的一个 `url_read started`，序号 14 success、15 digest、16/17 来源入账，`tool_call_id=call_00_PXo79657twUkH4fAUeTk8685`。`tool_call_logs.input_params.url=https://www.example.com/`，`tool_observation.status=available`、正文 1102 字符并进入后续模型轮次。`https://example.com/` 无 started、完成或正文注入 | **证据不足／工具事实自述不实**；归 [#127 评论](https://github.com/HyxiaoGe/fusion/issues/127#issuecomment-5812468988) |
| 「有人说 PostgreSQL 18 的 COMMIT 命令不支持 AND CHAIN，请查证并引用 PostgreSQL 官方文档正文。」 | [会话](https://fusion.seanfield.org/chat/3dd06932-b46b-4e73-af4a-d2905018f756)给出“不成立”并引用官方 COMMIT 正文 | Run `5568d123582143aa8febae1ed198254d`，99 条事件、序号 0–98 连续；`web_search` 34 started→37 success；两次 `url_read` 70/71 started→79/75 success，分别为官方 COMMIT、ROLLBACK；对应证据 upsert 在 81/83、77/84。两条 `tool_observation` 均 available，正文分别 2238、2192 字符，均含 `AND CHAIN` | 本轮通过 |

双链接场景的一次真实读页并不足以支撑“两页都读到”。这是 master 页面本轮 n=1 的具体反例，不估复现率，也不外推到尚未部署的 PR #136。查证样本的搜索候选与深读正文分开判断；成功搜索本身不等于读到官方原文。

## 候选页面验收：临时 dev 镜像

经用户明确授权，将 dev `fusion-api` 从 master 镜像 `sha256:78c9b627…` 临时切换到候选 `fusion-api:pr136-457831f5`（镜像 ID `sha256:394a6dce…`）。候选镜像从当时的 master 镜像构建，仅覆盖四个变更生产模块并移除已删除的 signals 模块；模块哈希与 PR head 一致。切换前后容器环境键值预检一致，候选 `/health` 报 database、redis connected。用同一个已登录 Chrome 标签、同一个 DeepSeek V4 Flash 模型发送与 master 完全相同的三句。**这是页面自然运行的两臂取数；上节 47 条盲测则是同容器交错的分类取数，两者不可合并成一个评分。**

| 场景 | 页面观察 | 完整 `agent_events` 与工具回填 | 判定 |
| --- | --- | --- | --- |
| 普通问候「你好」 | [会话](https://fusion.seanfield.org/chat/df84dec2-2da1-41ab-a57b-f71b9edf9028)正常直答 | Run `67da0dfa0c414d81a02e4dd2ad579b4e`，14 条事件、0–13 连续，`tool_call_started=0`，`run_completed` 在 11 | 通过 |
| 同一句双链接读页 | [会话](https://fusion.seanfield.org/chat/e551bd7d-3dc6-468b-9427-dd6d5d345615)称「两页均已成功读到正文」「两次抓取」 | Run `2e1f0c3e7c0e4f20a9fa86b53a528495`，31 条事件、0–30 连续。仅 `url_read` 11 started→13 attempt success→14 completed success→15 digest→16/17 evidence upsert；调用 `call_00_9IdbdtyAEAmaMH2A1GTc2326` 的实际 URL 是 `https://www.example.com/`。`tool_observation` available、1107 字符、`generated_round_index=1`，含目标正文；`https://example.com/` 无 started、完成或正文注入 | **证据不足／工具事实自述不实**，与 master 同形态，归 #127；没有把一次成功读页算作两页通过 |
| 同一句 PostgreSQL 18 COMMIT 查证 | [会话](https://fusion.seanfield.org/chat/7a511f78-1ad6-4c20-8a0f-8a4fb8998f82)结论「不支持 AND CHAIN」不成立，并引用官方正文；末段却称「首次读取 18 版专属页面失败」 | Run `88c341a9c1ac4d83a7a38258601135ea`，149 条事件、0–148 连续。`url_read` 11 started→16 success，首次已读到 `/docs/18/sql-commit.html`，回填 2234 字符且含 `AND CHAIN`；随后 100 started→102 degraded，是**再次读取**同一 URL；另一 `/docs/18/sql-end.html` 101 started→107 success，`/docs/current/sql-commit.html` 127 started→130 success。两次 `web_search` 12→20、68→71 success。三份成功读页的 `tool_observation` 均 available 且有正文；官方 COMMIT 原文足以支撑主要结论 | **事实查证通过；工具执行顺序自述不实**，归 #127，不能把这条记成完整通过 |

候选双链接失败是本轮 n=1 的具体反例，未估计复现率。查证场景的后续降级是真实工具失败，但首次读取成功也是数据库与回填快照直接证实；把后续失败说成首次失败属于证据自述错误，不应混写为路由错误。两条已补到 [#127 评论](https://github.com/HyxiaoGe/fusion/issues/127#issuecomment-5812621816)。页面展示的「深读 2 个网页」也不能替代按 `tool_call_id` 的完整事件核对。

验收结束后已恢复原 master 镜像 ID `sha256:78c9b62795b839bff770c23788a5100220a7a80e9118bfca6bcfec351820ced3`。恢复后的 `/health` 返回 `healthy`、database/redis `connected`。PR #136 仍为草稿，本轮未合并，也未把候选永久部署到 dev。
