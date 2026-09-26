# 语义正则迁移第一阶段：路由与工具边界

## 范围

从当前 `master@b4744dfa` 出发，移除请求文本上的出行计划、搜索意图、火车类型和网络否定词正则。普通路径由现有能力分类模型一次输出能力包、网络约束、禁止工具和跨产品主工具；服务端验证字段并在公告、恢复和执行边界裁剪。显式动态发现路径由首次 `tool_search` 声明约束，随后冻结目录。格式解析、已返回结构化事实的提取及安全边界保留。

本阶段没有增加分类模型调用次数。字段变多仍可能改变单次模型耗时、输出质量和 token 用量，需在运行态观察。

## 本地证据

- `python scripts/check_architecture.py`：通过，既有测试文件覆盖警告 4 项。
- `python -m ruff check .`、关键改动文件 `ruff format --check`、`git diff --check`：通过。
- 按 CI 收集命令 `python -m unittest discover -s test -t . -v`：3242 项通过，跳过 2 项。本机沙箱需允许测试绑定 `127.0.0.1` 临时端口。
- CI 额外 pytest 清单：241 项通过。独立静态复审未发现本次差异新增的可达 P0/P1；这不是运行态质量证明。
- 先前在 dev 已部署旧版本下记录 5 条分类基线；再对同一 dev 代理别名注入新分类提示词做单次原始模型探针：城际、天气加路线、禁网天气、禁搜索读 URL、可靠来源查证均返回可解析的新字段。此项只是提示词输出样本，不能代替新代码的真实 API 验收。

## dev 发布与真实 API

- PR [#148](https://github.com/HyxiaoGe/fusion/pull/148) 必需检查全绿，合并提交 `676c9fed69d6f00ffaae9e8dbd50da2dcd8a1338`。dev 工作流 [36210514071](https://github.com/HyxiaoGe/fusion/actions/runs/36210514071) 成功；API/UI 发布台账 `current_sha` 均为该提交。运行 API、knowledge worker、adapter、UI 镜像 ID 与各自发布台账一致。
- 用既有 dev 探针账号经真实 `/api/chat/send` 路径发送五条自然请求，均收到 HTTP 200 且 SSE 正常结束；再按探针账号所有权读取对应 `agent_sessions`、`agent_events` 与持久化消息。城际 run `1bd094c6a48e4bec894a6c73d50b52cb` 选择 `mobility_intercity`，`route_compare` 启动并成功；混合 run `ad3b94d991a04bbbaa1c315a49585d79` 选择 `mixed_itinerary`，只公告路线和天气等所需工具，没有额外公告航班或火车，`route_compare` 降级、`weather_forecast` 成功。
- 禁网 run `9eca2cafd70d434d953a0a3344b3f869` 降级为 `tools_unavailable`，外部工具公告和实际调用均为空，禁止列表含路线、天气、搜索及已授权 MCP 别名。禁搜索 run `27b5657d1b654376ba3990fcb6c2a000` 仅公告 `url_read`，无 `web_search` 调用；指定 URL 读取降级，最终 run 为 `incomplete`，诚实说明未完成核实。可靠来源 run `93ff9aeb01394f22b0bf94d1aab83b8e` 选择 `verified_web`，一次搜索及两次读取均成功。只读核对 `tool_call_logs.metadata` 中保存的实际正文：官方 COMMIT 页含 `commit the current transaction`，对应模型观察编号 `[3]`；官方 BEGIN 页含 `terminate a transaction block`，对应 `[8]`。持久化最终答复使用这两个编号说明结论。
- 禁网天气最终回答虽明确无法获取真实预报，仍补充了没有查询证据的“典型气候”数值。这是模型回答质量缺陷；后续补丁收紧现有无联网边界提示词，并需要在补丁部署后以同句复验。以上 API 证据也不替代页面验收。

## 保留风险

- 显式动态发现的禁用约束依赖模型在首次 `tool_search` 正确声明；服务端保证声明后的目录不放宽，但模型若直接回答或错误理解用户限制，本阶段不能保证语义正确。该路径保持显式 opt-in。
- 第一阶段 API 样本只覆盖上述五条；第二阶段产品答案及页面效果分别记录，不用这些样本推断全量语义准确率。
