# Run 能力路由混合分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Run 能力路由的“消息 → `_CandidateRoute`”从纯规则分类改为“字面层规则优先、语义层 LiteLLM 结构化分类”，同时保持能力解析骨架和既有契约完全不变。

**Architecture:** 新增独立模型分类器，在一次同步 LiteLLM 调用内输出严格 JSON，并把有限的模型标签确定性映射为 `_CandidateRoute`。通用路由入口继续保留可注入 `classify_fn` 和规则分类器，生产 Runner 显式注入混合分类器；模型缺失、超时、异常或非法输出一律 fail-closed 到 `clarification_only`，不在单次请求内退回语义规则。

**Tech Stack:** Python 3.11、LiteLLM 1.x、Pydantic v2、pytest、Ruff

**Spec:** `docs/specs/backend/2026-08-27-run-capability-router.md`

## Global Constraints

- 只替换“消息 → `_CandidateRoute`”步骤；不得改变 resolution 冻结、`validate_capability_resolution_semantics()`、definitions/handlers/bindings 原子派生、Skill 终态与 Trajectory 投影。
- 不得修改 `backend/test/fixtures/blind_routing_probe.json`。
- `backend/test/fixtures/agent_behavior_eval_samples.json` 必须全部通过，契约一致性不得回退。
- `abstract` 组必须保持 5/5，不得通过放松负例提高覆盖率。
- 模型默认 `deepseek-chat`；目标新增 P95 不高于 1 秒，硬超时 1.5 秒；每次最多一次调用、零重试；输入不超过 2,000 tokens，输出不超过 128 tokens。
- 上下文只携带当前消息与最近一个完整 user/assistant 轮次；超过输入预算时先丢弃历史上下文，当前请求仍超限则 fail-closed。
- 模型不可用、凭据缺失、超时、解析失败或返回非法能力时，返回 `clarification_only`；不得做请求内规则兜底。
- 不修改 `backend/app/services/stream/agent_plan_tool_policy.py`、`backend/app/services/stream/run_capability_contract.py` 及 Skill/Trajectory 相关文件。

---

## Task 1: 新增受预算约束的模型分类器

**Files:**

- Create: `backend/app/services/stream/run_capability_model_classifier.py`
- Create: `backend/test/services/stream/test_run_capability_model_classifier.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/app/ai/llm_observability.py`
- Modify: `backend/test/ai/test_llm_observability.py`

- [ ] **Step 1: 先写失败测试**

  覆盖以下行为：字面命中不调用模型；模型恰好调用一次且带 `timeout=1.5`、`num_retries=0`、`max_tokens=128`、`temperature=0`、JSON response format；天气与混合行程输出可确定性映射；只传最近完整轮次；超预算先丢上下文、当前消息仍超预算则不调用并 fail-closed；凭据缺失、超时、异常、畸形 JSON、未知 package、非法工具组合均 fail-closed 且不重试；全局禁网请求不能被模型提升为外部能力；日志不泄露原始消息。

- [ ] **Step 2: 运行测试并确认红灯来自缺失实现**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_model_classifier.py backend/test/ai/test_llm_observability.py -q
  ```

  Expected: 新模块或新接口尚不存在导致失败，而非测试自身语法错误。

- [ ] **Step 3: 实现最小模型分类器**

  在新模块中实现 `classify_capability_request_with_model(message, available_tools, conversation_messages=None)`。先调用 `_extract_request_signals()` 与 `_classify_literal_layer()`；未命中时构造受 2,000-token 预算约束的上下文，调用一次 `litellm.completion`，用 `extra="forbid"` 的 Pydantic 模型解析 `package_id` 与 `explicit_tool_names`，再映射到 `_CandidateRoute`。模型只允许选择 direct、transform、date、fresh/verified/url web、weather、place、mobility、flight、train、travel、mixed itinerary 与 clarification；不得选择 `deep_research`、`knowledge_grounded`、`tools_unavailable` 或 `mcp_explicit`。所有失败路径统一生成低置信度 `clarification_only`。

  新增配置：

  ```text
  RUN_CAPABILITY_CLASSIFIER_MODEL=deepseek-chat
  RUN_CAPABILITY_CLASSIFIER_TIMEOUT_SECONDS=1.5
  RUN_CAPABILITY_CLASSIFIER_MAX_INPUT_TOKENS=2000
  RUN_CAPABILITY_CLASSIFIER_MAX_OUTPUT_TOKENS=128
  RUN_CAPABILITY_CLASSIFIER_CONTEXT_TURNS=1
  ```

  直接使用 `litellm_proxy/<model>`、`LITELLM_PROXY_URL` 与 `LITELLM_API_KEY`，避免 `resolve_model()` 的独立远程发现超时。通过 `merge_litellm_kwargs()` 标记 `run_capability_classifier` 阶段，只记录耗时、结果、包名与低基数错误类型。

- [ ] **Step 4: 运行聚焦测试并确认绿灯**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_model_classifier.py backend/test/ai/test_llm_observability.py -q
  ```

  Expected: PASS。

- [ ] **Step 5: 提交**

  ```bash
  git add backend/app/services/stream/run_capability_model_classifier.py backend/test/services/stream/test_run_capability_model_classifier.py backend/app/core/config.py backend/.env.example backend/app/ai/llm_observability.py backend/test/ai/test_llm_observability.py
  git commit -m "feat: 新增能力路由模型分类器" -m "Co-Authored-By: Codex <noreply@openai.com>"
  ```

## Task 2: 接入字面层与生产 Runner

**Files:**

- Modify: `backend/app/services/stream/run_capability_router.py`
- Modify: `backend/app/services/stream/agent_loop_request_prep.py`
- Modify: `backend/app/services/stream/runner.py`
- Modify: `backend/test/services/stream/test_run_capability_router.py`
- Modify: `backend/test/services/stream/test_agent_loop_request_prep.py`
- Modify: `backend/test/test_stream_handler.py`

- [ ] **Step 1: 先写失败测试**

  增加以下断言：问候、身份询问、纯计算及精确 MCP alias 在字面层直接返回，不触发模型；`build_agent_loop_call_config()` 可接收并透传 `classify_fn`；生产 `runner` 用 `functools.partial` 显式绑定混合分类器；默认通用 builder 仍使用规则分类器以保持契约夹具确定性。

- [ ] **Step 2: 运行测试并确认红灯来自尚未接线**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/test_stream_handler.py -q
  ```

  Expected: 新增的字面层或注入接线断言失败。

- [ ] **Step 3: 完成接线且不改能力解析骨架**

  扩展 `_classify_literal_layer()` 以覆盖问候、身份、纯计算和精确 MCP alias；其余语义规则继续保留为显式回滚分类器。为 `build_agent_loop_call_config()` 增加可选 `classify_fn` 并原样传给 `resolve_run_capability_route()`。生产 `runner` 显式绑定 `classify_capability_request_with_model`。将 `ROUTER_VERSION` 更新为 `2026-09-04.1`。不得更改 `_validated_resolution()` 之后的任何能力解析与投影流程。

- [ ] **Step 4: 运行路由、接线与契约回归**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/test_stream_handler.py backend/test/test_agent_behavior_eval.py -q
  ```

  Expected: PASS，`agent_behavior_eval_samples.json` 全量通过。

- [ ] **Step 5: 提交**

  ```bash
  git add backend/app/services/stream/run_capability_router.py backend/app/services/stream/agent_loop_request_prep.py backend/app/services/stream/runner.py backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/test_stream_handler.py
  git commit -m "feat: 接入混合能力路由分类" -m "Co-Authored-By: Codex <noreply@openai.com>"
  ```

## Task 3: 更新盲测入口与规格

**Files:**

- Modify: `backend/scripts/blind_routing_probe.py`
- Create: `backend/test/scripts/test_blind_routing_probe.py`
- Modify: `docs/specs/backend/2026-08-27-run-capability-router.md`

- [ ] **Step 1: 先写失败测试**

  断言默认盲测入口使用混合分类器；`--classifier rules` 显式运行规则基线；默认模式缺少 LiteLLM 凭据时以清晰错误和非零状态退出；报告保持总覆盖率与分类别命中率；夹具路径和期望值未发生修改。

- [ ] **Step 2: 运行脚本测试并确认红灯**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/scripts/test_blind_routing_probe.py -q
  ```

  Expected: 新 CLI 行为尚不存在导致失败。

- [ ] **Step 3: 实现盲测入口并同步规格**

  默认按真实混合分类器执行；规则模式只用于复现 42% 基线与回滚诊断。规格记录已确认的模型、延迟、成本、上下文和 fail-closed 决策，明确通用规则分类器是显式回滚能力而非请求内兜底，并说明没有真实 LiteLLM 凭据时不得宣称准确率验收完成。

- [ ] **Step 4: 验证规则基线与缺凭据阻塞**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python backend/scripts/blind_routing_probe.py --classifier rules --verbose
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python backend/scripts/blind_routing_probe.py
  ```

  Expected: 规则模式为 14/33（42%），`abstract` 为 5/5；当前无凭据环境中的默认模式清晰失败，不输出误导性的混合准确率。

- [ ] **Step 5: 提交**

  ```bash
  git add backend/scripts/blind_routing_probe.py backend/test/scripts/test_blind_routing_probe.py docs/specs/backend/2026-08-27-run-capability-router.md
  git commit -m "test: 接入混合路由盲测入口" -m "Co-Authored-By: Codex <noreply@openai.com>"
  ```

## Task 4: 完整验证与真实模型验收

**Files:**

- Verify only; do not modify `backend/test/fixtures/blind_routing_probe.json`

- [ ] **Step 1: 运行静态检查与完整相关测试**

  Run:

  ```bash
  /Users/sean/code/fusion/fusion-api/.venv/bin/python -m ruff check backend/app/services/stream/run_capability_model_classifier.py backend/app/services/stream/run_capability_router.py backend/app/services/stream/agent_loop_request_prep.py backend/app/services/stream/runner.py backend/scripts/blind_routing_probe.py backend/test/services/stream/test_run_capability_model_classifier.py backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/test_stream_handler.py backend/test/scripts/test_blind_routing_probe.py
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_model_classifier.py backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/test_stream_handler.py backend/test/test_llm_observability.py backend/test/scripts/test_blind_routing_probe.py backend/test/test_agent_behavior_eval.py -q
  ```

  Expected: PASS。

- [ ] **Step 2: 审计硬约束与差异范围**

  Run:

  ```bash
  git diff origin/pr/28...HEAD -- backend/app/services/stream/agent_plan_tool_policy.py backend/app/services/stream/run_capability_contract.py backend/test/fixtures/blind_routing_probe.json backend/test/fixtures/agent_behavior_eval_samples.json
  git diff --check origin/pr/28...HEAD
  ```

  Expected: 四个受保护文件无差异，`git diff --check` 无输出。

- [ ] **Step 3: 使用真实 LiteLLM 环境运行验收命令**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" python backend/scripts/blind_routing_probe.py --verbose
  ```

  Expected: 相对 42% 基线显著提升，并输出改动前后的分类别对比；`abstract` 必须保持 5/5，所有 33 条用例必须使用原始期望值。

  若环境仍缺少 `LITELLM_PROXY_URL` 或 `LITELLM_API_KEY`，此步骤必须标记为 blocked，只能报告结构改造与 mock 测试结果，不得把任务描述为已完成验收。

- [ ] **Step 4: 独立代码复审**

  独立检查模型输出边界、fail-closed 行为、事件循环阻塞上限、日志脱敏、生产接线和骨架不变性。发现问题先修复并重新运行本任务全部验证，再形成最终交付说明。

## Task 5: 最终审查 P1 回归修复

**Files:**

- Modify: `backend/app/services/stream/agent_loop_wiring.py`
- Modify: `backend/app/services/stream/runner.py`
- Modify: `backend/app/services/stream/run_capability_router.py`
- Modify: `backend/test/services/stream/test_agent_loop_contract.py`
- Modify: `backend/test/services/stream/test_agent_loop_wiring.py`
- Modify: `backend/test/services/stream/test_run_capability_router.py`
- Modify: `backend/test/test_stream_handler.py`

- [x] **Step 1: 写入 P1 红灯回归测试**

  为 contract helper 通过 `build_call_config_fn=partial(build_agent_loop_call_config, classify_fn=classify_capability_request)` 注入确定性规则分类器；为 Runner 断言阻塞 builder 在线程中运行、1.5 秒 deadline 超时且关闭 session；为 alias 增加中文、英文和无分隔符的 alias 加天气/航班/地点负例，并保持纯 alias 命中。

- [x] **Step 2: 运行新增测试确认失败**

  Run:

  ```bash
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/test_stream_handler.py backend/test/services/stream/test_agent_loop_contract.py backend/test/services/stream/test_agent_loop_wiring.py backend/test/services/stream/test_run_capability_router.py -q
  ```

  Expected: 新线程组装 API、timeout 路径和 alias 组合边界均尚未满足。

- [x] **Step 3: 最小实现同步准备、线程配置和同步组装**

  在 `agent_loop_wiring.py` 拆出只在调用线程访问 `db` 的 prepared-input helper、无 `db` 参数的纯 call-config helper、以及复用 session 的同步 assembly helper；保留 `build_agent_loop_lifecycle_call()` 作为既有同步调用方的组合入口。Runner 在数据库准备完成后以 `asyncio.to_thread()` 加 `asyncio.wait_for(..., timeout=1.5)` 构建配置，再在原线程组装 execution/lifecycle，finally 始终关闭 session。字面 alias 仅在产品层对同一句无已成立产品工具时返回，其他请求交由既有产品/语义层。

- [x] **Step 4: 运行完整 P1 验证**

  Run:

  ```bash
  cd backend && DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m unittest discover -s test -t . -v
  DATABASE_URL="sqlite:///:memory:" /Users/sean/code/fusion/fusion-api/.venv/bin/python -m pytest backend/test/services/stream/test_run_capability_model_classifier.py backend/test/services/stream/test_run_capability_router.py backend/test/services/stream/test_agent_loop_request_prep.py backend/test/services/stream/test_agent_loop_wiring.py backend/test/services/stream/test_agent_loop_contract.py backend/test/test_stream_handler.py backend/test/test_agent_behavior_eval.py -q
  /Users/sean/code/fusion/fusion-api/.venv/bin/python -m ruff check backend
  ```

- [x] **Step 5: 审计并提交**

  检查 `git diff --check`、受保护 contract/Skill/Trajectory/fixture 路径与 `git status`；仅暂存本任务实现、测试、计划与 SDD 报告，使用中文提交信息和 `Co-Authored-By: Codex <noreply@openai.com>`。
