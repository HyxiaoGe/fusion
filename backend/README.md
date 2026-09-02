# Fusion Backend

Fusion Backend 是 monorepo 中的 FastAPI 应用，负责认证接入、会话与消息持久化、模型目录和治理、流式 Agent 执行、工具调用、文件处理以及知识库检索。

[返回项目首页](../README.md) · [聊天核心数据流](CHAT_CORE_DATA_FLOW.md) · [知识库运行手册](docs/KNOWLEDGE_BASE.md) · [模型验收手册](docs/MODEL_ACCEPTANCE_RUNBOOK.md)

## 主要能力

- **聊天与 Agent runtime**：多轮会话、SSE 流式输出、计划控制、工具调用、续跑和最终结果收敛。
- **模型目录与治理**：通过 LiteLLM 统一模型配置、健康状态、动态准入和运行时策略。
- **检索与证据**：网页搜索、页面读取、来源候选排序、证据账本和回答引用。
- **远程工具**：受主机、凭据引用、超时和调用预算约束的 MCP 工具，以及隔离的产品工具适配器。
- **文件与知识库**：本地、MinIO 或 OSS 存储；独立 Knowledge Worker 解析、切块、嵌入并写入 Milvus。
- **安全与运维**：OAuth/JWT 接入、撤销状态、管理审计、健康检查、迁移和发布回滚契约。

## 技术栈

- Python 3.12、FastAPI、Uvicorn
- SQLAlchemy、Alembic、PostgreSQL
- Redis、httpx、asyncio
- LiteLLM 与多模型供应商
- Milvus、MinIO / OSS
- pytest、Ruff、Docker

## 本地开发

### 1. 安装依赖

从仓库根目录开始：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境

```bash
cp .env.example .env
```

至少需要配置 PostgreSQL、Redis、Auth Service 和一个可用模型。知识库、远程 MCP、FlyAI、对象存储和模型治理默认关闭或需要额外基础设施，请按 `.env.example` 中的注释逐项启用。

### 3. 启动 API

```bash
uvicorn main:app --reload --port 8000
```

服务默认监听 `http://localhost:8000`。生产镜像使用多 worker Uvicorn；是否暴露 OpenAPI 文档由运行环境配置决定。

## 测试与静态检查

```bash
pip install -r requirements-ci.txt
ruff check .
pytest -q
```

需要复现 CI 的容器路径时，运行：

```bash
bash .github/scripts/linux-build-and-test.sh fusion-api-ci fusion-flyai-adapter-ci local
```

## 代码结构

```text
app/
├── api/          # FastAPI 路由与依赖
├── services/     # 聊天、Agent、工具、知识库和治理服务
├── ai/           # 模型目录、提示词、嵌入和适配器
├── db/           # ORM、Repository 与数据库连接
├── schemas/      # 请求、响应与内部协议
├── processor/    # 文件和图片处理
└── core/         # 配置、安全、Redis 与运行时基础设施

alembic/          # 数据库迁移
flyai-adapter/    # 隔离的第三方产品工具适配器
ops/              # LiteLLM 等运行配置
scripts/          # smoke、Worker 与维护脚本
test/             # pytest 测试
```

后端分层依赖保持 `API → Service → AI → Data`，详细约定见 [`docs/CODING_CONVENTIONS.md`](docs/CODING_CONVENTIONS.md)。

## 重要运行文档

- [聊天核心数据流](CHAT_CORE_DATA_FLOW.md)
- [知识库运行手册](docs/KNOWLEDGE_BASE.md)
- [LiteLLM 健康探测成本治理](docs/LITELLM_HEALTH.md)
- [模型验收手册](docs/MODEL_ACCEPTANCE_RUNBOOK.md)
- [Agent 轨迹设计](docs/TRAJECTORY_DESIGN.md)

## 发布与回滚

后端不再由独立旧仓发布。根工作流 [`deploy-dev.yml`](../.github/workflows/deploy-dev.yml) 负责在 `master` 上构建镜像、解析 repository digest、执行 Alembic migration、验证镜像身份与健康状态，并在 API 发布成功后继续 UI 发布。

每次接受的 API 发布都会记录提交 SHA、镜像引用、digest 和 image ID。手动回滚只能选择发布账本中唯一可解析的历史提交；数据库迁移遵循 expand / contract，镜像回滚不会执行 `alembic downgrade`。
