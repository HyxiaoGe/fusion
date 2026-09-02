# Fusion Backend

Fusion Backend 是 Fusion 的 FastAPI 应用，负责把一次用户任务转换为可持久、可恢复、可审计的模型与工具执行过程。

[返回项目首页](../README.md) · [聊天数据流](CHAT_CORE_DATA_FLOW.md) · [编码约定](docs/CODING_CONVENTIONS.md)

## 应用职责

- **会话与流式协议**：管理会话、消息和标题，通过 SSE 传输模型输出、Agent 事件、工具结果和恢复状态。
- **Run 能力路由**：在每个 Run 开始前冻结当前任务所需的能力包、工具集合、计划模式与 Prompt 片段。
- **Agent runtime**：执行多轮模型调用、计划控制和工具调用，并支持停止、续跑、重连与终态收敛。
- **检索与结构化工具**：接入网页搜索、页面读取、天气、地点、路线、航班、高铁，以及受白名单约束的远程 MCP 工具。
- **回答依据与轨迹**：记录脱敏的来源、工具摘要、上下文状态、模型请求和 Run 轨迹，供聊天与轨迹界面使用。
- **文件与知识库**：管理会话文件；知识库启用后，由独立 Worker 完成解析、切片、嵌入与 Milvus 写入。
- **治理与审计**：提供模型目录、运行配置、MCP 管理、服务用量和管理员只读审计接口。

## 主要依赖

- Python 3.12、FastAPI、Uvicorn
- PostgreSQL、SQLAlchemy、Alembic
- Redis、httpx、asyncio
- LiteLLM Proxy
- 可选：Milvus、MinIO / OSS、Search Service、Reader Service、远程 MCP 与 FlyAI Adapter

认证由独立 Auth Service 提供。模型目录和模型访问凭据由 LiteLLM 侧治理，Backend 不提供用户 BYOK 数据库。

## 本地开发

### 1. 创建环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 准备配置

```bash
cp .env.example .env
```

至少需要可访问的 PostgreSQL、Redis、Auth Service 和 LiteLLM。搜索、Reader、MCP、FlyAI、对象存储和知识库按需要配置；知识库默认关闭。

### 3. 启动 API

```bash
uvicorn main:app --reload --port 8000
```

默认地址为 `http://localhost:8000`。OpenAPI 是否开放由 `ENABLE_DOCS` 控制。

> `docker-compose.yml` 复用已有的 middleware、LiteLLM 和 Fusion Docker 网络，不是零依赖的一键安装方案。

## 测试

```bash
pip install -r requirements-ci.txt
ruff check .
pytest -q
```

需要复现容器测试路径时运行：

```bash
bash .github/scripts/linux-build-and-test.sh fusion-api-ci fusion-flyai-adapter-ci local
```

## 代码结构

```text
app/
├── api/          # HTTP 路由、依赖与响应边界
├── services/     # 聊天、Agent、工具、知识库与治理服务
├── ai/           # 模型目录、Prompt 与嵌入适配
├── db/           # ORM、Repository 与数据库连接
├── schemas/      # 请求、响应和内部协议
├── processor/    # 文件与图片处理
└── core/         # 配置、安全、Redis 与运行时设施

alembic/          # 数据库迁移
flyai-adapter/    # 隔离的第三方出行工具适配器
ops/              # LiteLLM 等运行配置
scripts/          # smoke、Worker 与维护脚本
test/             # pytest 测试
```

后端分层保持 `API → Service → AI → Data`。

## 深入文档

- [聊天核心数据流](CHAT_CORE_DATA_FLOW.md)
- [知识库运行手册](docs/KNOWLEDGE_BASE.md)
- [Agent 轨迹设计](docs/TRAJECTORY_DESIGN.md)
- [LiteLLM 健康探测成本治理](docs/LITELLM_HEALTH.md)
- [模型验收手册](docs/MODEL_ACCEPTANCE_RUNBOOK.md)
- [发布安全边界](docs/RELEASE_SAFETY.md)
