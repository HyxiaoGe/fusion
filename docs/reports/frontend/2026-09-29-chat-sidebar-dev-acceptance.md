# 对话侧边栏美化 dev 发布与页面验收

日期：2026-09-29（Asia/Shanghai）

## 交付版本

- 变更：全宽“新对话”入口、搜索交互与清除按钮、会话行选中和悬停状态、列表独立滚动，以及移动侧边栏背景与初始状态修正。
- 实现提交：`a44e06f0d82adec2464c0de046450be818a0c588`；[PR #199](https://github.com/HyxiaoGe/fusion/pull/199) 合并提交：`6357af5317499c7200bd45e7153f961c77428bf7`。
- [PR CI](https://github.com/HyxiaoGe/fusion/actions/runs/36548769031) 与[合并后的 master CI](https://github.com/HyxiaoGe/fusion/actions/runs/36549460250) 均成功，包括 UI 测试、生产 Docker 构建和 required gate。API 验证因本次仅修改前端而跳过。
- [dev 发布工作流](https://github.com/HyxiaoGe/fusion/actions/runs/36549460894) 成功，UI 候选冒烟、页面冒烟和发布台账步骤完成；API 部署跳过。

## 运行版本核对

只读检查 dev 发布台账与运行容器：UI 台账 `current_sha` 为 `6357af5317499c7200bd45e7153f961c77428bf7`；运行中 `fusion-ui` 的镜像 ID 为 `sha256:31e86885bd14c6e9f2d4ddf266767299a8dc8929d86c388932372049f3cfa640`，与台账一致；运行镜像的 RepoDigest 为 `sha256:76c021dc23a820914aa28e9c600c3407715dfa0848f8b0020d9d7227984419e9`，也与台账一致。容器运行中，重启次数为 0。dev 主机映射的 UI `/chat/new` 与 API `/health` 均返回 HTTP 200；本次 API 镜像未变。

## 真实页面验收

复用用户已有登录态的 Chrome Fusion 标签，刷新 `/chat/new` 后观察到新的全宽“新对话”入口和真实历史会话列表。`⌘K` 聚焦侧边栏搜索，输入“上下文窗口解释”后显示两条真实匹配会话，关键词高亮；清除搜索可恢复完整分组列表。点击第一条结果可打开对应历史对话，选中态可见；其“更多操作”菜单在列表之外完整显示重命名、删除对话和生成标题选项，未执行这些操作。验收后关闭菜单、清除搜索，并返回原来的 `/chat/new` 页面。未发送消息或修改会话数据。

## 本地检查与边界

改动相关 24 项测试、目标 ESLint 与 `git diff --check` 通过。本地隔离浏览器检查了桌面明暗主题、移动布局、搜索快捷键和清除交互，没有发现横向溢出或移动端 hydration 报错。dev 登录态浏览器本轮实测的是桌面页面；移动端和暗色模式的 dev 页面效果尚未单独验收。
