# 动态工具发现：生产接入边界（只列清单，本轮不迁移）

日期：2026-09-22。依据当前工作树代码阅读与 P08 离线集成测试，不是生产验收。

## 入口与授权目录

- **已有承载**：聊天入口 `backend/app/services/stream/runner.py` → `agent_loop_wiring.prepare_agent_loop_call_config_inputs` → `build_agent_loop_call_config`。`options` 来自请求；`is_dynamic_tool_discovery_enabled` 要求 `options["dynamic_tool_discovery"] is True`，默认关闭。动态工具定义/handler 来自 MCP `load_mcp_agent_tools`；别名目录来自 `load_mcp_authorized_tool_aliases`（`agent_loop_wiring.py`、`app/services/mcp/agent_tools.py`）。用户原文只进 `original_message`，不能把未授权名字写进 catalog。
- **未验证或缺口**：生产请求目前没有稳定的产品开关把 `dynamic_tool_discovery` 设为 True。实验 fixture 目录与账号 MCP 目录不是同一对象。用户输入扩大目录的否定测试只在原型 opt-in 下做过。
- **下一步最小工作**：在 wiring 增加受控、默认关的 options 透传（若产品决定灰度），并确认账号 MCP 授权列表作为唯一 catalog 来源。
- **验收**：默认请求 `dynamic_tool_discovery is False`；opt-in 时 `authorized_tool_names` 与 MCP 别名一致，用户句子不能新增 alias。
- **回退**：不传该 option 即旧包分类路径。

## 运行态

- **已有承载**：`tool_search.promote` 同步 schema、handler、plan allow-set、公告工具（`dynamic_tool_discovery.py` + `tool_round.py`）。网络否定走既有 `_resolve_network_scope`。预算对象在 fixture 中共享、不重建；driver 取消/deadline 沿 `check_agent_loop_limit`。本轮发现路径 stop 一律 `defer_output`。
- **未验证或缺口**：生产 MCP handler 与实验假工具的预算/幂等未在同一 Run 上对过。计划模式 + 发现仅原型测过。
- **下一步最小工作**：用真实 MCP 授权集跑 P01–P07 等价集成，确认 promote 后 `call_kwargs.tools`、bindings、plan enum 一致。
- **验收**：未加载拦截可恢复；未授权永不执行；重复发现幂等且不重置 `run_start`。
- **回退**：关掉 opt-in；进行中的发现 Run 应走完当前 defer 提交，不要中途换包。

## 产品交付

- **已有承载**：正常 stop → `handle_agent_round_outcome` → `_commit_deferred_answer`（产品 validator / grounded / 无证据守卫）。触顶 → `run_limit_summary_step` + `_guard_no_evidence_answer`（`defer_output=True`）。工具失败恢复 → 现有 tool_issue / recovery_evidence。P08 离线矩阵见 `dynamic-tool-discovery-p08-report.md`。
- **未验证或缺口**：自然语言未被守卫覆盖的具体事实；天气 fixture 日期缺口下的“正确说覆盖不足”依赖 grounded/validator 而非通用理解；忠实转述可不带 synthetic limitations。
- **下一步最小工作**：真实供应商结果块上复跑 P08 矩阵；核对 empty success 块的卡片展示。
- **验收**：不安全候选从未出现在 `append_chunk(..., answering)` 与终态 `content_blocks` 文本；问候与用户数字不被误拦。
- **回退**：`PRODUCT_ANSWER_REPAIR_ENABLED` 默认 False 未改；关发现 opt-in 回到包路径 defer 规则。

## 持久化与事件

- **已有承载**：过程 checkpoint `tool_round.persist_tool_round_checkpoint` → `persist_message`（`persistence.py`）。终态 `agent_loop_run_completion.persist_run_message` → `finalize_completed_run` → `run_finalizer.complete_agent_run`（`run_completed` + session status）。轨迹 `_run_config` 在发现路径写 `dynamic_tool_discovery` 而不写假 package（`agent_loop_lifecycle.py`）。
- **未验证或缺口**：Redis SSE 恢复、历史快照读回、部分写入 CAS 与发现工具变化的组合。P08 测试用内存 store + 捕获 `append_chunk`，不写真实 Redis/DB。
- **下一步最小工作**：对照现有 `test_persistence.py` / `test_reliable_termination_persistence.py`，加一条发现路径终态块含 weather/train 的落库形状。
- **验收**：终态事件 `finish_reason`/`session_status` 与保存文本一致；旧 Run 不被新请求续写。
- **回退**：发现字段仅出现在启用时的 run config；旧会话无该键。

## 前端

- **已有承载**：`frontend/src/types/trajectory.ts` 的 `package_id` 为封闭联合 + unknown；`normalizeTrajectoryEvent.ts` 校验 `capability_resolution`/`package_id` 形状；`trajectoryNodeDetailModel.ts` 按节点展示，不解析发现 catalog。
- **未验证或缺口**：UI 仍按 `activation_source: capability_package` 消费。发现路径 `capability_resolution=None`、轨迹写 `dynamic_tool_discovery` 对象时，前端如何展示未测。本轮未改 UI。
- **下一步最小工作**：只读核对未知 `package_id` 与缺 resolution 时轨迹页是否可渲染；需要产品文案再改。
- **验收**：不因缺少 package 丢弃整条 run；不把 catalog 当成已查询。
- **回退**：不发前端；旧包 resolution 不变。

## Skill / 深度研究 / 续跑

- **已有承载**：opt-in 遇 Skill pin、`task_mode=deep_research`、`previous_run_id`/continuation 抛 `DynamicToolDiscoveryUnsupportedError`（`dynamic_tool_discovery.py` + `agent_loop_request_prep.py`）。测试 `test_unsupported_scenes_refuse_instead_of_silent_fallback`。
- **未验证或缺口**：生产 runner 接到该异常后的用户可见错误文案。
- **下一步最小工作**：把异常映射到现有 `ApiException`/SSE 错误码，仍拒绝而不是回退分类。
- **验收**：上述场景零分类调用、零静默旧路径。
- **回退**：保持拒绝。

## 灰度 / 回退

- **已有承载**：默认关。关 option 即包分类器 + 原 defer 规则。
- **未验证或缺口**：进行中会话中途打开/关闭的混用；历史消息里发现工具块在关开关后的展示。
- **下一步最小工作**：先验证授权目录与 P08 交付，再对内账号打开 option；不要批量删旧 package。
- **验收**：灰度账号可关即回；历史包路径会话仍可刷新。
- **回退**：配置层本轮不改、不部署。

## 后续真实验收（本轮未执行）

聊天 HTTP/SSE、用户现有 Chrome 登录态、真实天气/车次/搜索供应商、取消、刷新、恢复。本轮消耗模型额度 0。
