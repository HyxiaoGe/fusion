# Issue #57：Agent 反馈与证据修复记录

日期：2026-09-08（Asia/Shanghai）。基线应用为 `origin/master@21fefbcca81ebfe6623fa6044433f5bd11bf295b`；开发分支 `codex/issue-57-agent-feedback`，保留此前 `f71fedb5` 文档提交。

本轮落实用户批准的四块工作：恢复判定、搜索反馈与控制清理、实际 Observation 可观测性、质量回归。保留自实现循环和本地英文模板，无新框架、提示词服务或数据库迁移。

## 行为变化

| 范围 | 修复后行为 | 验证入口 |
| --- | --- | --- |
| 正常答案保留 | failed/degraded/success 分开累计；有实际有效网页证据时，普通联网及领域工具失败后的联网答案不会仅因缺少或写错引用编号被整段替换。 | `test_tool_failure_recovery.py`、`test_tool_recovery_integration.py` |
| 无证据收尾 | 工具异常仍作为反馈；保留一次替代工具恢复机会，全部无有效结果后明确未完成。知识库、深研及成功产品结果沿用各自约束。 | `test_tool_failure_recovery.py`、`test_tool_recovery_evidence.py` |
| 搜索反馈 | 成功、空结果和失败均回显 query；可用时传递发布日期与站点，缺失明确 Unknown；安全及引用规则每条工具消息出现一次，搜索和读页使用相同全局编号。 | `test_search_feedback_observation.py` |
| 搜索控制 | 去掉关键词决定的阅读上限/下限、媒体白名单和优先级处方；保留提供方原序及 URL 去重。相同查询不会被旧 guard 直接跳过并制造 degraded。硬总额与 URL 安全措施保留。 | `test_source_candidate_ranker.py`、`test_network_budget.py` |
| 原始链接与来源身份 | 一个共享身份键贯穿搜索、跨批编号、distinct read 与研究完成度；实际链接和读取参数保留原 URL。旧显式 evidence ID/引用编号兼容，跟踪参数变体不再凑足研究来源数。 | `test_source_url_identity.py` |
| 归属提示 | 数字、金额、时间和地点要对应具体主体及事件；页面日期不等于事件日期，相关背景不等于本次事实，缺少测算依据允许未知。没有针对鱼类问题特判。 | `runtime_prompts.toml` 与新固定证据样本 |
| Observation | 在实际 tool message 完成格式化及追加指导后采集，存入已有工具日志；展示原始业务返回与实际反馈。标明凭据遮盖、截断、未采集及失败；不声称这是供应商最终 HTTP 请求。 | `test_observation_trajectory.py`、`test_trajectory_api.py`、前端 trajectory 测试 |
| 上下文范围 | 每轮记录应用裁剪前、保留及移除的 tool_call_id 和完整计数，编号列表有界；不把“曾取得的来源”表述为“本轮仍在模型上下文”。 | `test_observation_trajectory.py`、`test_agent_round.py` |

## 独立审查中追加修复

1. 原始 URL 保留后，搜索与下游证据使用了不同去重规则；审查复现同页 `utm_id` 变体获得两个编号并错误满足两来源要求。统一身份规则，补齐 citation/used 事件及研究恢复对原 URL 的保留。原审查附件从 3 项失败变为 3 项通过。
2. Observation 补写不能仅以 asyncio 等待超时声称释放数据库线程。独立审查用阻塞数据库复现默认线程池耗尽；修复复用现有 `TrajectoryRecorder` 专用 4 线程和准入上限，真实 worker 结束才释放容量。延迟落库测试改为等待真实提交事件。
3. 回放器必须记录完整实际请求而非仅 messages：增加工具声明、固定证据综合的 `tool_choice=none`、max_tokens、请求摘要、脚本/应用/样本摘要及实际返回模型。抑制 HTTP 客户端默认日志中的内部地址。
4. 现有 CI 先跑 unittest 再显式选择 pytest 文件；本轮 7 个新增或修改的函数式测试入口已追加到 Linux/Windows 脚本，并有容器入口契约测试保护。

## 质量回归方法及已知结果

`backend/scripts/agent_feedback_eval.py` 默认 dry-run，不调用模型。`--apply` 使用现有 LiteLLM，基于生产 formatter 构造固定工具历史，采集真实模型综合答案。它不执行实时搜索，不评估自主检索/结束决策，不等于自然登录页面验收。完整请求只保存模型参数，不包含连接地址或 Authorization。

6 个样本包括鱼类原例、数量依据追问，以及水库治理、科研资助、投资状态、全部无来源四个独立构造例。鱼类证据来自[黄河水利委员会转载](http://yrcc.gov.cn/xwdt/lylw/202608/t20260831_453312.html)与[光明日报](https://epaper.gmw.cn/gmrb/html/content/202608/31/content_23722.html)的人工事实摘录，不伪称抓取的完整原文；构造例明确标注并使用 example.org。评审条件不进入模型输入，引用格式正确也只标记 `requires_review`，不自动判定事实正确。

公开 master 的首次回放共 12 条：`deepseek-chat` 与 `mimo-v2.5-pro-ultraspeed` 各 6 条。独立审阅发现 6 条符合评审条件、2 条内容失败、4 条仅输出工具协议。内容失败均来自 DeepSeek：将直岗拉卡 5 万尾并入另一家三江公司的专项行动；对未公开的数量依据作过度确定的解释。4 条协议输出受到早期回放器未声明工具的影响，不能归因为部署产品故障，也不能计入新旧质量比较。

后续单条 `deepseek-reasoner` 协议 smoke 已能交付正文，但模型别名与前一批不同，仅证明回放协议可工作。最终适配器另加了完整请求及代码身份记录。以上都属于公开基线，不能证明候选修复改善了模型回答质量。

候选源码复制到既有 dev 临时目录的动作被自动审批拒绝，理由是未发布源码发送到该目的地需要明确授权。已提出仅临时隔离测试、不部署且不传输凭据的授权请求；在获得答复前不绕过。候选真实模型回放与部署页面验收仍是缺口。

## 检查记录

- 后端完整 pytest：4,042 项、4,492 项子测试通过，2 项既有跳过，9 条既有弃用警告。首轮 4,041 通过、1 失败来自旧恢复提示词字面断言；更新语义断言后，该文件 37 项通过，再完整重跑得到上述结果。
- 前端完整 Vitest：208 文件、2,482 项通过。
- 前端生产构建：最终文案改动后重跑通过；相关组件及 normalizer 151 项通过。
- 全量 TypeScript：25 条既有错误；与未修改基线输出逐字一致，无新增错误。
- 回放器：10 项通过；新增请求记录及日志保护用例先失败后修复。
- 两平台 CI 容器入口：12 项、22 项子测试通过。
- 完整后端 Ruff、59 个修改 Python 文件格式检查、修改前端文件 ESLint、架构检查和 diff 检查通过；部署脚本契约 20 项通过，Linux shell 语法通过。Windows 脚本仅做静态/契约验证，未运行原生 PowerShell 或容器 CI。
- 四块独立审查均闭环，修复审查发现的来源身份、真实线程容量和回放可复核性问题后，未遗留本轮引入的可达 P0/P1。

主要集成命令：

```sh
# backend/，仅内存数据库与受控测试外部边界
DATABASE_URL=sqlite:///:memory: PYTHONPATH=$PWD python -m pytest test -q -p no:cacheprovider
ruff check .
python scripts/check_architecture.py

# frontend/
npm test
npm run build

# 回放器默认预览，不访问模型
python scripts/agent_feedback_eval.py
```

本地详细审查及原始模型输出另保留在 `.superpowers/sdd/2026-09-08-issue-57-agent-feedback/` 和 `/private/tmp/fusion57-*.jsonl`；它们不进入应用运行或测试期望。可移植的 fixture、回放器和复现测试已随代码交付。

## 对原评审的澄清与保留事项

- `degraded` 触发答案替换的问题已证实，但“任意一次 degraded 必然覆盖”不准确；旧行为还受结果先后、显式引用、有效来源与产品结果影响。新回归覆盖结果六排列。
- 有有效来源不意味着答案每个事实正确；不新增自动答案改写器，也不把正则主体共现当作语义质量证明。
- 页面发布日期和站点名可帮助阅读，但不能唯一证明事件时间或主体。当前 search-service 有 `published_at`，尚未提供 `site_name`，因此后者真实结果保持 Unknown。
- 保留 `infer_search_intent`：它仍被 `_is_verified_research_request` 使用。删除了无读取点的预算、repair、排序和阅读处方配置；旧 JSON 额外字段仍可解析，但不再必填。
- 保留原 1,000 字符搜索摘要、8,000 字符读页窗口及上下文裁剪机制；本轮增加实际反馈及移除范围可见性，未实现长文分页或新的记忆压缩机制。
- 原链接保留仍受既有读取安全策略约束：读页请求可移除默认端口及 fragment；本轮不再把去重用的 host/path/query 改写反灌到请求或展示。
- 旧产品回答供应商名称正则与无证据固定中文收尾不在本轮重构范围。后者仍存在非中文用户本地化缺口，没有额外新增模型收尾调用或语言猜测规则。
- 历史轨迹不重建不存在的 Observation；新记录的有界采集仍可能失败，不应展示为已完整采集。
- 迟到的 Observation 补写不会自动刷新一个已经打开的详情面板，稍后重新打开或刷新可见。文案明确“当前尚无”，不把暂未取得错误地表述成永久未采集；未新增轮询状态机。

## 交付边界

本报告仅记录本轮本地开发与验证，未代表 push、PR、远端 CI、合并或部署。发布仍需单独授权。部署后的自然回归需覆盖鱼类问题、投资分析、领域工具失败转联网、全无来源，以及 Observation/引用/裁剪范围在刷新后的恢复。
