# 查证来源证据策略与终局正文保留（2026-09-23）

时间口径为 Asia/Shanghai。本报告分别记录代码检查、CI、dev 运行身份、服务内校验和真实页面结果；默认动态工具发现尚未切换。

## 改动与代码验证

- PR [#123](https://github.com/HyxiaoGe/fusion/pull/123) 将明确查证请求的来源义务拆为独立 `verified_web_v1` 策略，仅在显式开启动态工具发现时使用。交付前要求本 Run 的 `url_read` 有可提取正文，且最终引用编号对应已读来源；搜索摘要、URL 或工具成功状态本身不放行。旧包路由和默认入口保持原状。提交 `89d6a241`，PR CI `35844795287` 通过，合并为 `64e1d1fe`。
- 合并后 master CI `35845326559` 通过。dev 发布 `35845327138` 首次运行在 Windows runner 安装 CI 依赖时遇到 PyPI JSON 解析错误；失败作业重跑后发布成功。dev API 台账指向 `64e1d1fe`，当时 API 与 worker 的 image ID 均为 `sha256:3e1c55cc1c35cd4507055edaad161b46c5f7b7b4a9478de747b13a2d05c1dc0a`，健康接口返回 200。
- 在这版实际运行的 `fusion-api` 容器中执行只读内存断言：明确查证信号识别成功；没有读页正文时拒绝、引用错误编号时拒绝、有正文且引用匹配时放行。此检查证明部署镜像内策略逻辑生效，不等同于动态发现真实模型端到端验收。

## 首次页面回归发现的问题

- 18:07 在用户现有 Chrome 扩展标签内新建会话 `79de1cb1-2955-44e8-9550-8f7b2f51c49e`，请求核验 PostgreSQL 官方 COMMIT 与 ROLLBACK 页面。旧 `verified_web` 包路由执行 1 次 `web_search`、2 次 `url_read`，Run 完成。两个工具结果及其模型 Observation 都含完整的页面正文，包括语法、Description、Parameters、Notes；页面显示深读 2 个网页。
- 最终回答却称“正文被截断，只能确认页面开头”。最终模型轮输入为 2,458 Token，轨迹的工具上下文显示保留 0 条工具消息。代码定位为普通终局总结在仅含 `update_plan`、`web_search`、`url_read` 事务时清掉全部工具消息，留下较短来源投影。工具结果完整与模型最终实际可见内容因此产生落差；这条真实页面回归不能判为通过。
- PR [#124](https://github.com/HyxiaoGe/fusion/pull/124) 仅让查证类终局总结保留已格式化的整组 `assistant/tool` 消息，继续移除过期的 Skill 工具控制提示；其他任务和深度研究的总结清理规则不变。回归同时覆盖旧 `verified_web` 包与新 `verified_web_v1` 策略。提交 `544178eb`，本地总结测试 70 passed、38 subtests passed；相关动态发现、事实守卫和请求准备测试 181 passed。需监听本机回环端口的独立协议用例在允许绑定后单独通过。Ruff、格式与差异检查通过。

## 最终发布与真实验收

- PR #124 的 CI `35847808096` 中 API 验证、安全检查和必需门禁成功，UI 验证因无前端改动跳过。PR 合并为 `10a95e7e`；master CI `35848263337`、dev 发布 `35848263945` 均成功，UI 发布跳过。没有 GitHub 正式代码审查记录。
- dev API 发布台账 `current_sha=10a95e7e490a1f37490caba2b38674190d24c2ba`，API digest `sha256:d0f6cdcc21d95760c82b4809361a847dd138bc45d65c8e678caf05a27d5400b2`，image ID `sha256:818c16c96fab3001fbf12bf4388f8c7f0a1a1f408fb10c4f30da905cb6029300`。`fusion-api`、`fusion-knowledge-worker` 均运行这一 image ID，重启次数为 0；健康接口 200，数据库和 Redis 均连接。
- 18:28 在同一 Chrome 标签新建会话 `44fb961e-6848-476b-b463-cf04331dd1cf`，原样重发 PostgreSQL 查证请求。旧 `verified_web` 路由在 12.04 秒完成，搜索 1 次、读页 2 次。最终模型轮输入 6,494 Token，轨迹显示 5 条工具消息全部保留、移除 0 条；回答给出 COMMIT/ROLLBACK 正文中的语法和行为，并分别引用实际读页编号 `[3]`、`[6]`。刷新后回答、来源链接和“轨迹完整”仍保留。这条默认查证路径的目标回归通过。

## 边界与下一步

页面默认发送仍走旧包路由，因前端没有自然入口设置 `options.dynamic_tool_discovery=true`；所以本次真实页面证据不覆盖新 `verified_web_v1` 路径的供应商端到端执行。该策略已有本地脚本回归与部署容器内校验，但默认入口切换前仍应按迁移判据处理 Skill 固定版本、深度研究、续跑及授权边界，再做受控真实请求。不能把这次查证修复写成动态工具发现已默认替换。
