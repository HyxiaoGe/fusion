# 停止轨迹修复验证记录

## 根因与改动

PR #49 发布后，真实天气结果出现后停止并刷新，结果卡片保留，但最后 LLM 取消和 Run 中断事件被 Redis 所有权门禁挡住，未进入 trajectory。原始对话、Run 和事件序号见 [首次发布验收](2026-09-07-agent-loop-dev-release.md)。

- CompositeWriter 仅在 StreamOwnershipLostError 时把明确取消 terminal 送入该 Run 的原轨迹 recorder，跳过 progress，继续重抛原异常。Redis 不解冻，其他消息、工具结果、计划和非所有权故障保持原行为。
- LLM 取消时即使实时流已冻结，也完成一次性原候选详情调度；普通轮次/总结轮次把所有权失效归入取消，保留调用方原异常。
- 无数据库或共享事件协议变化，无前端代码变化，无历史记录回填。

## 本地验证

- 红测复现：真实 emitter + LLM lifecycle + queued recorder + SQLite 新 Session 查询，已输出/无正文两种取消原先丢 terminal；普通/总结所有权竞态与 superseded 账本矩阵也失败。随后完成最小修复。
- 核心目标 12 文件：`315 passed + 82 subtests`；归因、详情、查询和安全 payload 4 文件：`60 passed + 91 subtests`。合计 `375 passed + 173 subtests`。
- 使用既有 `/Users/sean/code/fusion/fusion-api/.venv/bin/python`，从 backend 目录以 `TZ=Asia/Shanghai LITELLM_LOCAL_MODEL_COST_MAP=True` 运行。早期临时 Python 环境缺少 auth_service_client 导致停止接口测试收集失败，切回完整环境后通过；未修改依赖或鉴权实现。
- 九个变动 Python 文件 Ruff check / format check 通过，架构检查通过（原有四项测试覆盖提示保留），diff check 通过。
- 独立审查无可达 P0/P1；业务与测试差异 SHA-256 `ca2b2380f7773c664082d78a051eeca845a4626dc376b2d5e2f0e39200e6f801`，四个业务文件差异 SHA-256 `eeaa4fef36168549faaef0949b1db0ff1d482b7740e494cd84f353600740320f`。

## 发布与真实复验

- 仓库 `HyxiaoGe/fusion`，分支 `codex/agent-loop-stop-trajectory`，审查/PR head `c61ffb583f7d764e945eea3c30103938bea53db4`。
- [PR #50](https://github.com/HyxiaoGe/fusion/pull/50) 于北京时间 2026-09-07 13:04:31 合并，master / 发布 SHA `7a93c7fffb5108b0a0b5aa6b991a2cd62e78a00f`。合并后 backend/frontend/ops/.github 与审查版本没有差异。
- [PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/34084963675) 和 [master CI](https://github.com/HyxiaoGe/fusion/actions/runs/34085415461) 的 Detect changes、安全检查、API validation、UI validation 和 required gate 均通过。
- [dev 发布](https://github.com/HyxiaoGe/fusion/actions/runs/34085415682) 已完成 API 的 Windows 测试/镜像推送、dev 替换、镜像身份/健康/冒烟及 accepted-release 记录。13:10:41 独立核对 API 为新 SHA，UI 当时仍为 `11906526`，服务均健康、重启均 0。此次没有前端业务代码差异，先在新 API 上做真实复验，UI 发布结果及发布后刷新另行追加。

### 真实停止复验：13:11–13:12

复用原 Chrome 标签与登录态，DeepSeek V4 Flash、思考开启、自动执行；重新提交同一个杭州天气/西湖散步问题，真实 `weather_forecast` 卡片可见后点击停止。

- [修复后的停止对话](https://fusion.seanfield.org/chat/1c5ef7b8-99c7-4244-a193-05b9a29940e6)，Run `7751ec243378462181570a8d8ab3a75d`，2 轮模型、1 次真实工具。
- 停止后和刷新后天气卡片文本均与点击停止前完全相同。运行 interrupted，轨迹 complete。
- 完整轨迹 23 条事件，sequence 0–22 连续；sequence 21 是 `llm_round_cancelled`，22 是 `run_interrupted`。`expected_last_sequence=22`、`degraded_reason=null`，末轮 `terminal_source=recorded`，不再由 Run 推断取消。
- 末轮模型详情为「已取消」，正文归因为 `suppressed/none/no_content`，对应「未输出 · 无正文输出 · 该轮没有正文候选」。取消前捕获的部分推理仍可在预览读取，详情 HTTP 200；这次在正文尚未产生时停止，不冒充已输出正文的真实取消验收。
- 发送、会话、轨迹、stream-status 和 node-detail 观察到 HTTP 200，导航/停止过程中存在 `ERR_ABORTED`；本次取消请求本身没有捕获到完整 HTTP 响应，不标注为 stop HTTP 200。服务器持久 Run/取消事件与真实刷新结果共同证实终止生效。console error/warn 为空。

### 最终发布与刷新验收：13:14–13:15

- dev 流水线最终 `success`：API 和 UI 的 Windows 测试/镜像推送、dev 替换、身份/健康/冒烟、accepted-release 均通过；UI 最后完成于北京时间 13:14:19。没有触发失败回滚。API checkout 曾出现一次 GitHub TLS 握手错误，流水线重试后成功，未跳过检查。
- 13:14:43 独立读取 API/UI accepted-release，二者 current_sha 均为 `7a93c7fffb5108b0a0b5aa6b991a2cd62e78a00f`，运行镜像与各自台账一致，重启均为 0。
- API image ID：`sha256:63538ce8c09c133fe39c13378efc88a02fe057ed9ca076c354761be88bd95d6e`，仓库 digest：`sha256:3472621812ddca4692403cde159cc8dd60e6c910ee1c08ae3cfd7720419319e7`。
- UI 无源码改动，镜像内容沿用 `sha256:07be5713da6e000e2f97bf3dcb86988fce23addee0d3f7551644627f92b0b5c2`，仓库 digest：`sha256:001f2b9ad298128a5d97a5249364e962a46dc636f9b50754241b6bf904dbc14a`，该镜像已由本次发布重新验收并记入新 SHA。
- API healthy，database/redis connected，UI HTTP 200。随后在同一登录态浏览器再次刷新修复后的停止对话：天气卡片逐字一致、运行已中断、轨迹完整、末轮已取消且 suppressed/none/no_content 保留。本次完整网络观察窗口未截断，11 个 API 响应均为 200、无 loadingFailed，console error/warn 为空。

本次明确的停止终态缺口已完成代码、CI、dev 和真实浏览器闭环。新建自然用例保留供用户查看，历史失败 Run 未回填；最终部署后的文档证据作为本地补充提交保存，不为文档重复发版。

## 保留范围

工具执行中取消后产生的普通 failed 结果或计划快照仍可能因 Redis 拒绝留下轨迹缺口，继续如实降级。此次修复保证明确取消事件的记录，不声称全部 stop 场景轨迹完整；多城市路由与产品回答改写策略未修改。
