# 语义正则迁移第十阶段：天气风力范围分隔符

## 真实触发

第九阶段 dev 的原有骑行请求 run `39e70965905544c98ee649afd4492ebb` 查询明天深圳市天气和上午避雨。模型候选正确说明逐日预报无法确认上午是否下雨，但使用非断行连字符写出 `风力1‑3级`；事实校验将 `1‑3` 的首个数字错误地识别为独立风力值，返回 `weather_fact_mismatch`，用户最终只收到未回答上午条件的四日摘要。

## 修改与验证

- 风力数值范围解析接受 U+2011 非断行连字符，并归一化为普通连字符后与结构化预报字段逐值核对；错误范围 `1‑5级` 仍被拒绝。没有新增用户意图或天气建议的词面规则。
- 真实模型候选和结果快照在相同代码路径本地重放：修复前 `weather_fact_mismatch`，修复后 `ok`。天气校验目标测试 73 项通过；完整 unittest 3256 项通过、2 项跳过；额外 CI pytest 241 项通过；Ruff 和架构检查通过。独立复审未发现新增可达 P0/P1。
- PR [#157](https://github.com/HyxiaoGe/fusion/pull/157) 的 API、UI、安全与必需门禁均通过，合并为 `4d9eb24303abc13049401cb63806c7a10a2fc0c7`。master CI `36274820517` 首次仅 API validation 中轨迹记录测试 `test_prompt_metadata_and_effective_request_fingerprint_survive_database_reload` 失败：等待第二条事件落库时出现 `event_wait` 超时，实际只读到一条。失败 job 重跑后 API validation 与必需门禁通过，工作流最终成功；该次超时的根因尚未确认。
- dev 工作流 `36274820845` 成功。API/UI 发布台账与当前 SHA 均为 `4d9eb243`；API 容器镜像 ID `sha256:d745dd4dce5527b300198174698d57c85eeba15fdd22a6b80fcc6d31a6407dc6`、UI 容器镜像 ID `sha256:54d9db201af08e4f4d6d652ea553acb4d0ef8a0a8704e7f2402611a59ca5cee1` 与各自发布台账一致。UI 内容镜像与上一阶段相同，digest 未变。
- 固定日期的原有骑行请求在真实 dev API run `4d4e68712a9e4b66b2330ca669e99e50`（会话 `fed883b8-35cd-4d77-8f3c-eacf20602356`）HTTP 200、SSE 完成、天气工具成功。最终回答说明 9 月 28 日深圳逐日预报及风力范围，并明确无法从逐日数据确认上午是否下雨；轨迹为 `source=model, reason=deferred, disposition=emitted`，确认本轮是模型直接交付。预报数值会随时间更新，此处验证的是回答路径与证据边界。
- 范围外日期 run `746f62d3e36340a2a2f5eaf3dcab37b6`（会话 `f52007ff-15e3-45c5-8983-76977db531c2`）HTTP 200、SSE 完成、天气工具成功；最终准确说明返回预报覆盖 9 月 27—30 日、10 月 20 日无法确认。轨迹为 `source=server, reason=product_guard, disposition=replaced`，仍是服务端事实兜底，不计为模型直答。
- 原有 Chrome 当前未提供已登录的 Fusion 标签，因此页面验收尚无证据；API 验收不能代替页面验收。
