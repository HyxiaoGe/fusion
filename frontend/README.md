# Fusion Frontend

Fusion Frontend 是基于 Next.js 和 Electron 的聊天客户端，负责登录后的会话体验、流式消息渲染、Agent 轨迹、模型选择、文件与知识库入口，以及管理界面。

[返回项目首页](../README.md) · [聊天数据流](CHAT_UI_DATA_FLOW.md) · [前端架构](docs/ARCHITECTURE.md) · [架构约束](docs/ARCHITECTURE_RULES.md)

## 主要能力

- 多模型聊天、深度思考与自动执行模式
- Markdown、代码块、工具过程和来源证据渲染
- Agent 状态、步骤、轨迹和上下文用量展示
- 服务端会话历史与刷新恢复
- 文件上传、会话文件和知识库选择
- 模型、提示词、MCP、审计等管理入口
- 中文 / 英文、亮色 / 暗色主题
- Web 与 Electron 桌面客户端

## 技术栈

- Next.js 15、React 19、TypeScript
- Electron 34
- Redux Toolkit、Dexie
- Tailwind CSS、Radix UI、shadcn/ui
- TipTap、FilePond、React Dropzone
- Vitest、ESLint、Docker

## 本地开发

### 1. 安装依赖

从仓库根目录开始：

```bash
cd frontend
npm ci
```

### 2. 配置环境

```bash
cp .env.example .env.local
```

`API_BACKEND_URL` 只在 Next.js 服务端用于 `/api/*` 代理；`NEXT_PUBLIC_AUTH_*` 会进入浏览器 bundle，只能填写可公开的认证端点和客户端标识，不得放入密钥。

### 3. 启动客户端

```bash
# 仅启动 Web
npm run dev:next

# 同时启动 Next.js 与 Electron
npm run dev
```

Web 默认地址为 `http://localhost:3000`。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `npm run dev:next` | 启动 Next.js 开发服务器 |
| `npm run dev` | 启动 Next.js 与 Electron |
| `npm run lint` | 运行 ESLint，禁止 warning |
| `npm test` | 运行 Vitest 测试 |
| `npm run build` | 构建 Next.js 生产产物 |
| `npm run build:electron` | 构建 Next.js 与 Electron 安装包 |
| `npm run analyze` | 分析已有 bundle |

## 代码结构

```text
src/
├── app/           # Next.js 页面、布局和路由
├── components/    # 聊天、设置、管理与通用组件
├── electron/      # Electron 主进程
├── lib/           # API、流式协议和工具函数
├── redux/         # 全局状态与聊天状态机
└── scripts/       # 前端维护和分析脚本

public/            # 静态资源
docs/              # 架构与编码约定
scripts/           # 仓库级前端脚本
```

聊天状态必须按 `SSE → Redux → 渲染 → Dexie / 刷新恢复` 完整核对。IndexedDB 只作为本地缓存，服务端会话和消息才是产品真源。详细约束见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 与 [`docs/ARCHITECTURE_RULES.md`](docs/ARCHITECTURE_RULES.md)。

## 测试与构建

```bash
npm run lint
npm test
npm run build
```

CI 还会分别构建 Docker `test` 与 `production` target，验证生产镜像使用的环境变量、Next.js rewrites 和运行产物。

## 发布

前端不再由独立旧仓发布。根工作流 [`deploy-dev.yml`](../.github/workflows/deploy-dev.yml) 会在 API 发布成功后构建并部署 UI，通过容器内 HTTP smoke、浏览器 smoke、镜像 identity 和 UI 发布账本确认结果。
