# 回放评测用例

每个 YAML 文件是一个类别，用例格式与检查类型见 `app/evals/cases.py`，运行方式见 `scripts/run_eval_suite.py`。

写用例的约定：

- **来源必填**：`source` 写清是哪次探针、事故或 PR 发现的问题，复盘时能追溯。
- **只写期望行为，不写标准答案**：该调哪个工具、参数是什么、不能做什么；能用轨迹判的不交给裁判模型。
- **judge 评分标准要可判定**：写成"回答做到了 X，且没有 Y"，涉及事实时要求与工具结果一致。
- **不用绝对日期**：写"明天""后天""下周五"，否则用例会随时间过期。
- **规则有争议时先确认**：期望行为与现有产品规则不一致时，先和 Sean 确认再入库。

## 部署门禁

`master` 部署成功后，若本次改动涉及 Agent 行为（流式与 Agent loop、对话服务、工具、MCP、`app/ai` 下的提示词与 Skills、评测本身，
范围见 `.github/scripts/detect_changes.py` 的 `AGENT_EVAL_PATH_PREFIXES`），`Fusion dev deploy` 的 `Agent eval gate`
会在 dev 的 fusion-api 容器内跑全部用例。默认模型 `mimo-v2.6-pro,qwen3.8-flash`，可用仓库变量 `EVAL_GATE_MODELS` 调整。

- 出现退步（同一用例在同一模型上此前通过、本轮失败）、当场复核一次仍失败时工作流标红，不自动回滚，由人判断。
- 复核通过的记为"不稳定"，列在报告里但不标红；两次运行都入库（`attempt` 1/2），对比基准取复核结果。
- 门禁排在 UI 部署之后运行：dev 只有一台 Linux runner，避免长时间评测堵住 UI 部署。
- 新增用例第一次失败不算退步；`error`（超时、裁判故障等基础设施问题）也不算。
- 每次结果存入 `eval_suite_runs` / `eval_case_results`，可按 `case_id`、`model_id` 回看历史与轨迹快照。
