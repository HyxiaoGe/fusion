# 执行模式选择面板视觉补齐

日期：2026-09-30（Asia/Shanghai）。用户指出执行模式选择框遗漏了视觉优化；沿此前持续优化并直接发布的授权完成本轮。

## 改动与边界

原面板仍采用普通下拉菜单材质，与输入区及模型面板的 Liquid Glass 方向不一致。本轮仅修改 `ChatInput.tsx` 的样式挂载和新增局部 `ComposerAgentMode.module.css`，不修改三种模式的选择、持久化、能力判定或知识库冲突逻辑。

- 触发器增加轻玻璃底材、边缘高光和打开态；展开箭头随开合旋转。
- 浮层使用 20px 圆角、分层阴影和模糊底材；文字区保留足够稳定的底色。
- 三个选项统一为玻璃卡片；蓝色边界、图标和勾选标记识别选中态，键盘高亮保持独立反馈。
- 深浅色分别定义材质，保留窄屏最大宽度和浮层可用高度；减少动态效果与不支持 backdrop-filter 的环境有降级样式。

## 验证与发布

- 85 项相关测试通过，包含三种模式、键盘操作与禁用原因的既有边界；目标 ESLint 通过。
- PR CI 的前端全量 lint 与测试成功，测试文件 215 个通过、1 个跳过；Docker 生产镜像构建成功。
- 本地构建通过；仓库构建跳过类型检查，不据此宣称全量类型检查通过。
- 本地静态样式使用实际 CSS、Lucide 图标和当前前景色 token 检查深浅色展示，截图 `/private/tmp/fusion-mode-static.png`；静态展示不替代实际登录页面。
- PR [#212](https://github.com/HyxiaoGe/fusion/pull/212)，源提交 `6d20dd4328e018a9ba47196d413754c85f566a81`；CI [36663774924](https://github.com/HyxiaoGe/fusion/actions/runs/36663774924) 和全部 required checks 成功。11:25:56 合并为 `0c02cdca8f0137e04a3cdc935be22330d6526911`（Asia/Shanghai）。
- dev 发布 [36664365771](https://github.com/HyxiaoGe/fusion/actions/runs/36664365771) 与 master CI [36664365146](https://github.com/HyxiaoGe/fusion/actions/runs/36664365146) 成功。master CI 的 CLI watch 曾因 EOF 退出，随后直接查询确认工作流最终成功。
- 本次仅部署 UI，API 发布正常跳过。UI 于 11:31:54 accepted（Asia/Shanghai），台账与运行容器 SHA 为 `0c02cdca8f0137e04a3cdc935be22330d6526911`，容器 running，ref/digest/image ID 一致。
- UI digest：`sha256:0762fc44cb2aef601010f3e652490df9525747f69960db2561d76eb86e53b314`；image ID：`sha256:af61a5df807c490e64f83f77a1d3fde298e1bb04554e95a414494d906b0647d5`。发布健康、browser smoke 和 accepted release 步骤成功。

## 原有 Chrome 登录态实测

复用官方扩展的原标签、sean 登录态和浅色主题，在现有会话 `c7301b0c-7d06-4b77-951a-32677988a230` 验证，不发送新消息。

- 页面实测浮层为 20px 圆角、约 328px 宽，计算样式为 `blur(22px) saturate(1.5)`；截图在打开动画完成、opacity 为 1 时保存。
- 自动、计划、深度研究三种模式切换正常，对应菜单项的 `aria-checked` 与触发器文案一致。
- 从自动项按 ArrowDown 实测计划项键盘高亮，Enter 能选择计划；Escape 关闭后焦点返回触发器。查询关闭结果时等待退出动画完成，未把动画期间的临时 DOM 状态当作失败。
- 已恢复原有自动模式与空输入框，保留原 URL。完整页面截图 `/private/tmp/fusion-mode-dev-full.jpg`，按实际面板边界裁切的展示截图 `/private/tmp/fusion-mode-dev-detail.jpg`。
- 浅色为实际页面证据，深色为本地静态样式证据；禁用模式和窄屏边界继续由既有测试与样式限制覆盖，本次未额外做真实禁用模型或窄屏操作。

工作区已有上一轮发布后报告补记和未跟踪预览目录均保留。本轮提交仅包含两个前端源文件；发布后证据按实际结果补记，不为文档另触发一次共享部署。
