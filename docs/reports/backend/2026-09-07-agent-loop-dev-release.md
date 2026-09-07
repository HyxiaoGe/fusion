# Agent loop 首批 dev 发布记录

用户在本地开发验收后明确要求「那你发布吧，我看看效果」。本次通过现有 master → dev 流水线发布 API 与 UI，未使用其他发布环境。

## 发布身份

- 仓库：`HyxiaoGe/fusion`；开发分支：`codex/agent-loop-reliability`。
- PR [#49](https://github.com/HyxiaoGe/fusion/pull/49)，审查/PR CI head：`7a20d127cd9b848eed5d0f572fc6e82ed8f5405f`。
- master 合并/实际部署 SHA：`11906526a49907e70b8c0133342b5524e2c57faf`；合并时间：北京时间 2026-09-07 09:06:52。
- 前后端部署完成时间：北京时间 2026-09-07 09:17:21。
- 已读取 dev 的 API/UI accepted-release 台账，二者 current_sha 与上面 SHA 相同；运行容器的 image ID 和 digest 引用均与台账对应。

| 应用 | 运行镜像内容 ID | 仓库 digest | 运行状态 |
| --- | --- | --- | --- |
| API | `sha256:9acb18582402d161348c45d90aff3f278e87d9755d8dee757bc6f1a841a9a0e1` | `sha256:6fe4a915435272f911d3a34bc97f1434f3d76e3c7edda3deca24e7e9439dd73b` | 运行中，重启 0 次 |
| UI | `sha256:07be5713da6e000e2f97bf3dcb86988fce23addee0d3f7551644627f92b0b5c2` | `sha256:001f2b9ad298128a5d97a5249364e962a46dc636f9b50754241b6bf904dbc14a` | 运行中，重启 0 次 |

## 门禁与实际检查

- 发布前后端目标 `557 passed + 194 subtests`、前端 `192 passed`、根契约 `67 tests OK`，改动静态检查与 production build 通过；完整 tsc 保留原有 25 处错误，无新增。独立审查及最终取消缺口复审无剩余 P0/P1。
- [PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/34071451174)：API validation、UI validation、Workflow security validation、Detect changes、Fusion required gate 均成功。
- [master CI](https://github.com/HyxiaoGe/fusion/actions/runs/34071945145)：上述五个门禁均成功；已核对合并提交的 backend/frontend/ops 内容与 PR head 完全一致。
- [dev 部署](https://github.com/HyxiaoGe/fusion/actions/runs/34071945449)：API Windows 测试/镜像推送、dev 替换、镜像身份/健康/冒烟、accepted-release 记录均成功；随后 UI Windows 测试/镜像推送、dev 替换、候选健康和浏览器冒烟、accepted-release 记录均成功。未触发失败回滚。
- 独立只读验证：API `healthy`，数据库和 Redis 均 `connected`；实际 API 镜像的详情 DTO 包含 output_provenance、emitted/suppressed/replaced 与 tool_retracted；UI 主机 3004 端口 `/chat/new` 返回 200。
- 公开地址 [Fusion 新对话](https://fusion.seanfield.org/chat/new) 返回 HTTP 200；页面实际引用的 `/_next/static/chunks/3454-5780951feab5ba71.js` 包含 outputProvenance 和「正文输出归因」文案。

## 登录态真实验收（北京时间 2026-09-07 12:33–12:41）

用户解锁后，复用已登录 Chrome 的 Fusion 标签，使用 DeepSeek V4 Flash、思考开启、自动执行。12:35:27 再次只读确认 API/UI 仍是上述 SHA、镜像一致、重启均为 0，API/数据库/Redis 健康。

| 自然用户路径 | 实际结果与刷新验证 |
| --- | --- |
| [生活中的反馈循环](https://fusion.seanfield.org/chat/a1ba4a61-53aa-4664-a518-fabae7176f05) | Run `38c5e08693b047ba9de47c6e0e2e4620`，1 轮模型、无工具、完成。模型详情显示「已输出 · 模型 · 模型正文已流式写出」；模型原正文与聊天答案一致；刷新后答案、归因、原正文仍一致。 |
| [南京玄武湖今晚天气](https://fusion.seanfield.org/chat/deebf176-cefe-4303-8270-99458e5bc46a) | Run `f97f3cdfefd44218b1f8046f501f90fb`，2 轮模型、1 次真实 weather_forecast、完成。第 1 轮 suppressed/none/tool_round，第 2 轮 replaced/server/product_guard。天气卡片、服务端天气摘要、替换标记和原模型散步建议刷新后均保留。 |
| [多城市天气与散步](https://fusion.seanfield.org/chat/f4c22281-523c-436e-999c-ddca9ed9259a) | 首个请求被现有路由判为 clarification_only，0 次工具，未完成实时天气比较；不能计为工具能力验收。后续杭州问题在切换新对话的导航尚未完成时提交到了该会话，保留记录，不计入独立停止用例。 |
| [杭州天气结果后停止](https://fusion.seanfield.org/chat/86269e3b-1039-4f85-ae34-f088b8cf10a6) | Run `cadadcd66a674a31aee127ca46d1e78d`，真实天气卡片出现后点击停止，stop HTTP 200。刷新后卡片文本完全一致，状态 interrupted。暴露取消轨迹缺口：记录 0–20、期望到 22，finalize_mismatch；末轮状态由 Run 推断取消，归因未知。此项仅数据保存通过，完整观测未通过，进入专项修复。 |

- 普通回答的 stream、会话、轨迹、node-detail 请求，以及天气发送、轨迹、node-detail、停止与刷新请求均观察到 HTTP 200。首个对话导航的事件缓冲曾截断，不能声称完整捕获所有网络请求。
- 页面切换/刷新记录到 `net::ERR_ABORTED`；头像代理曾 HTTP 408，刷新后恢复 HTTP 200。用户浏览器 console error/warn 返回空列表，未观察到阻塞本次功能的错误。
- 这次真实场景还证明：模型给出的散步建议会被既有 product_guard 替换成天气事实摘要；归因面板忠实显示替换来源，并保留原候选供分析。本轮没有修改该产品策略，也没有修改多城市路由。
- 取消补修见 [停止轨迹实施计划](../../implementation-plans/2026-09-07-agent-stop-trajectory.md)。失败/工具执行中取消/计划中断/预算触顶等其他分支仍以原隔离回归证据为准，不把当前成功用例扩大为全部边界通过。

初次验收曾受其他任务占用、标签失效和 Mac 锁屏阻塞；解锁后未新开浏览器目标、未读取用户 token。旧历史没有归因元数据时显示未知仍是预期。

上表中的取消终态缺口已由 PR #50 专项修复；新 API 上重做同一天气停止场景，卡片保留、23 条连续事件、明确取消归因与刷新恢复均通过，详见 [停止轨迹修复验证](2026-09-07-agent-stop-trajectory.md)。上表原始失败记录作为发布后发现问题的证据保留，不修改旧 Run。
