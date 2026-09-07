# 工具载荷和结果可观测性修复记录

## 交付状态

2026-09-07（Asia/Shanghai），仓库 `HyxiaoGe/fusion`，分支 `codex/trajectory-tool-details`。用户明确要求“发布吧”后，完成提交、PR/CI、合并和 dev API/UI 发布。PR [#51](https://github.com/HyxiaoGe/fusion/pull/51) 的审查 HEAD 为 `2cddede64912cd5b2635c42744e168f8c7fbfda7`，合并/部署版本为 `f7880b2461239329edd89e6a32cb0591d93163a1`；两者 tree 均为 `6e3d564831e8e2f0c816973bf1b43b4354ee678d`。新天气、网页读取与旧摘要提示已在用户原有登录态 Chrome 标签页验收，刷新恢复通过。

## 用户可见行为

- 后续调用的 trajectory 载荷/结果读取实际 handler 入参与 `ToolResult.data` 的业务快照，天气城市和预报、网页正文、MCP 业务字段均不再受管理员审计白名单裁剪。
- 只对明确凭据字段、URL 凭据及正文中可识别的凭据形态遮盖；普通查询参数、`content`、`messages` 等业务内容保留。分别返回 `redacted_fields`、`truncated_fields`。
- 历史无快照的日志维持原摘要投影，并提示“此记录仅保存了工具摘要，未记录完整载荷和结果”。已记录但格式损坏的快照明确降级。
- 记录层级是工具 handler 的输入与业务返回；外部提供方原有规范化仍存在，本次没有声称捕获原始 HTTP 字节或最终格式化后送入 LLM 的精确 Observation。

## 实施边界

- 使用已有 `ToolCallLog.extra_metadata.trajectory_detail` JSON 字段，统一 `BaseToolHandler.log` 在日志调度前复制快照；未新增表或迁移。
- 原 `input_params/output_data` 审计摘要规则维持既有行为；工具执行对象、模型输入、SSE 账本及调用次数不变。快照异常只记安全错误类型与损坏标记，不改变原工具结果。
- 快照业务区段预算为入参 24 KiB、结果 72 KiB、错误 4 KiB，含字段路径元数据不超过 128 KiB；字符串至多 32 Ki 字符、容器 200 项、深度 12。截断保留前部内容并标明路径。
- `ToolCallLog.extra_metadata` 默认延迟加载，列表/聚合读取摘要；精确详情按会话、用户、Run、tool_call_id 显式读取。SQL 回归核对返回列，不把只返回数字的 count 子查询误判成读取正文。
- 管理员普通审计列表不输出新快照；既有管理员精确详情入口仍按原管理员鉴权、审计理由和访问记录提供对应详情。
- 旧版本可忽略新增 JSON；回滚不需要删数据，历史未保存内容不回填。

## 验证证据

后端命令使用 `/Users/sean/code/fusion/fusion-api/.venv/bin/python`，工作目录 `backend/`，`TZ=Asia/Shanghai LITELLM_LOCAL_MODEL_COST_MAP=True`。

1. 先添加 5 项真实 handler 日志写入 → SQLite → 新 Session 详情读取回归，旧实现全部失败，复现天气参数只剩 `argument_count`、网页正文缺失、截断/历史/损坏状态不明确。随后修复；新增列表加载回归也先失败后通过。
2. 最终核心回归：`python -m pytest test/services/test_trajectory_node_detail_service.py test/test_tool_handlers.py test/test_trajectory_api.py test/services/test_trajectory_query_service.py test/test_admin_audit_security.py test/test_admin_audit_service.py test/test_admin_audit_repository.py test/test_admin_audit_models.py test/test_trajectory_architecture.py test/services/test_tool_detail_snapshot.py -q` → **155 passed + 79 subtests**。
3. 工具执行关联回归：`python -m pytest test/services/test_tool_detail_snapshot.py test/test_mcp_agent_tools.py test/test_amap_product_tools.py test/test_flyai_travel_tools.py test/test_tool_executor.py -q` → **231 passed + 110 subtests**。与上一组重叠 11 项快照测试，不重复计数。
4. 新快照 11 项 `unittest.TestCase` 也通过 unittest discover，纳入现有 CI discovery，无新增 CI 白名单。
5. 前端先新增历史摘要/损坏提示测试，旧实现 2 项失败；实现后 `vitest run src/components/chat/trajectory/TrajectoryNodeDetailPanel.test.tsx` → **52 passed**。改动 TSX 的 ESLint 通过，`npm run build` 成功。
6. 8 个后端改动文件 Ruff check/format 通过，架构检查通过，`git diff --check` 通过。
7. 独立只读审查未发现当前改动可达的 P0/P1；独立天气刷新读取、URL 正文/凭据、管理员列表查询 3 项通过。

现有工具链提示：Pydantic/SQLAlchemy 废弃提示、架构脚本既有缺测试文件警告、Browserslist 数据过旧。前端构建配置跳过完整 TypeScript 检查，未将构建成功表述为全仓类型检查通过。没有启动本地 Fusion 服务，也没有用隔离测试替代线上验收。

## 发布与环境核验

- [PR CI 34091804685](https://github.com/HyxiaoGe/fusion/actions/runs/34091804685)：变更识别、工作流安全、API、UI、required gate 均成功。
- [master CI 34092432594](https://github.com/HyxiaoGe/fusion/actions/runs/34092432594)：API、UI、工作流安全与 required gate 均成功。
- [dev 34092432979](https://github.com/HyxiaoGe/fusion/actions/runs/34092432979)：API 参数契约、Windows 构建推送、dev 部署、发布结果，以及 UI 参数契约、Windows 构建推送、dev 部署均成功。API 部署完成于 14:54:20，UI 完成于 15:00:57，流水线 15:00:58 完成；不适用的交叉实现 job 按设计跳过。
- 15:04:19 独立核对 API/UI release ledger accepted SHA 均为 `f7880b24`，容器实际 image ID 匹配、运行中、重启数均为 0。API `/health` healthy，数据库/Redis connected；UI 实际映射端口 3004 的 `/chat/new` 为 HTTP 200，根路径按应用行为返回 307。

| 服务 | 实际 image ID | 发布 digest |
| --- | --- | --- |
| API | `sha256:d5fe72643627585441ce23eb9554ad5be492379221c1a10ad24ae81a8e143ecf` | `sha256:83de65bc0dccfdfc6197a4f1a561467a2e1d33f2f8394c8ab105c5b8d8270946` |
| UI | `sha256:631691ec2e2ecacf23c0f07b79a508277c22584fdef0a46c116a42d80c5d5401` | `sha256:6cd901f55ede324f5549bb79aa07e49d08bbab77bf5b24756e39edc5a81d064d` |

## 真实登录态验收

复用用户已打开的 Chrome 标签页 `643084497`，没有新建浏览器、标签页或隔离上下文。

1. [苏州天气](https://fusion.seanfield.org/chat/4d15a3bd-ec33-4297-a75b-656b0fdabaf3)：自然提问查询苏州天气与金鸡湖散步建议，Run `c17bb89098b34c049bf71c719835b93a`，tool_call `call_00_WfW7NPJhlmjtOZWht0f43726`。2 次模型调用、1 次真实天气工具，轨迹完整；载荷展示 `location=苏州`、`location_source=named`，结果展示 4 天预报及 handler 原有诊断字段。详情 `available`，`reason=null`，脱敏和截断列表均为空；刷新后载荷/结果文本完全一致。
2. [Python 官方文档搜索和阅读](https://fusion.seanfield.org/chat/b77962c0-dfae-4160-b840-d9eeb92ad764)：自然请求查找官方文档并阅读 TaskGroup 失败行为，Run `2f744d2c334a476e9e6107597c2aad92`。6 次模型调用、2 次搜索、2 次读取，轨迹完整。第一个读取 tool_call `call_00_8WuPVvyg07imch9mqVzy9458` 的载荷包含 URL 和读取原因；结果包含标题、正文等字段，原始 `content_length=50207`，快照正文保留 32768 字符。详情 `available`，`redacted_fields=[]`，仅 `truncated_fields=[result.content]`，页面明确提示“部分正文已截断”。最终 UI 发布后刷新，载荷/结果文本完全一致。
3. [历史天气记录](https://fusion.seanfield.org/chat/1c5ef7b8-99c7-4244-a193-05b9a29940e6)：旧 Run `7751ec243378462181570a8d8ab3a75d` 仍显示原 `argument_count=2` 摘要，新 UI 明确提示“此记录仅保存了工具摘要，未记录完整载荷和结果”。未伪造或回填旧正文。

已观察到的页面、会话、Run、trajectory 和精确工具详情 API 返回 200，console error/warn 为空。天气刷新窗口完整且无 loadingFailed；网页刷新窗口 API 响应均为 200，其中一个 `/api/models/` 请求随后被页面取消（`net::ERR_ABORTED`），模型显示和详情恢复正常。长 SSE 阶段的早期网络缓冲有截断，不把它表述为全量网络请求验证。

验收边界：直接粘贴 URL 的另一个用例走了预读取，没有产生工具节点，未计入工具详情验收。天气回答仍受既有 `product_guard` 替换为事实摘要，未完整回答穿衣建议；网页用例存在既有计划校验后的重新搜索。本次仅修复工具详情可见性，未扩修这些产品行为。
