# 工具载荷和结果可观测性修复记录

## 交付状态

2026-09-07（Asia/Shanghai），分支 `codex/trajectory-tool-details`，实现起点 `3bfa9b03`。已完成本地实现、定向回归、生产构建与独立审查；用户随后明确要求“发布吧”，现进入提交、PR/CI、合并、dev 部署和真实工具调用/刷新验收。最终部署版本与现场证据在完成后补记。

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
