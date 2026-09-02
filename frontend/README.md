# Fusion Frontend

Fusion Frontend 是 Fusion 的 Next.js / Electron 客户端，负责把模型输出、Agent 执行、回答依据和历史状态组织成完整的任务体验。

[返回项目首页](../README.md) · [聊天数据流](CHAT_UI_DATA_FLOW.md) · [前端架构](docs/ARCHITECTURE.md)

## 用户界面

- 任务首页、灵感入口和 Prompt 模板库
- 多模型选择、能力标签、思考模式与自动执行模式
- SSE 流式聊天、Markdown、代码、文件和结构化工具结果
- 回答依据、网页来源、知识库引用与来源详情侧栏
- Agent 进度、计划、工具摘要、停止与继续执行
- 独立轨迹视图，以及聊天与轨迹之间的双向定位
- 会话历史、搜索、重命名、刷新恢复与上下文状态
- AI 个性化、数据管理和知识库设置
- 管理员模型、运行配置、MCP、用量与审计入口
- 中文 / 英文、亮色 / 暗色主题，以及 Electron 桌面构建

## 主要技术

- Next.js 15、React 19、TypeScript
- Redux Toolkit、Dexie
- Tailwind CSS、Radix UI、shadcn/ui
- Vitest、Testing Library、ESLint
- Electron、Docker

## 本地开发

### 1. 安装依赖

```bash
npm ci
```

### 2. 准备配置

```bash
cp .env.example .env.local
```

`API_BACKEND_URL` 只在 Next.js 服务端用于 `/api/*` 代理。`NEXT_PUBLIC_AUTH_*` 会进入浏览器 bundle，只能填写允许公开的认证端点和客户端标识，不能放入密钥。

### 3. 启动客户端

```bash
# Web
npm run dev:next

# Next.js + Electron
npm run dev
```

Web 默认地址为 `http://localhost:3000`。登录、模型和工具链路需要可用的 Fusion Backend 与 Auth Service。

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
├── app/           # 页面、布局和路由
├── components/    # 聊天、轨迹、设置、管理与通用组件
├── electron/      # Electron 主进程
├── hooks/         # 聊天、认证、知识库和轨迹行为
├── lib/           # API、SSE 协议、路由与工具函数
├── redux/         # 全局状态与聊天状态机
└── types/         # 前端协议类型

public/            # 静态资源
docs/              # 架构与编码约定
scripts/           # 构建与维护脚本
```

聊天主状态按 `SSE → Redux → 渲染 → Dexie / 刷新恢复` 核对。Dexie 只提供本地缓存，服务端会话、消息、Run 和轨迹才是产品事实源。

## 测试与构建

```bash
npm run lint
npm test
npm run build
```

容器构建还会分别验证 `test` 与 `production` target，确保生产环境变量、Next.js rewrites 和运行产物一致。

## 深入文档

- [聊天数据流](CHAT_UI_DATA_FLOW.md)
- [前端架构](docs/ARCHITECTURE.md)
- [架构约束](docs/ARCHITECTURE_RULES.md)
- [编码约定](docs/CODING_CONVENTIONS.md)
