# Liquid Glass 视觉层次优化与 dev 验收

日期：2026-09-30，Asia/Shanghai。用户授权优化一版并发布 dev，供其查看效果。

## 修改与边界

- 搜索、模板卡片和模型入口降低常态填充、边框及透镜强度。
- 今日灵感的胶囊进一步降低常态高光，保持尺寸、轮播与点击行为。
- 输入区增加稳定底材、工具栏分层和聚焦边界，拖拽时仍显示主色虚线。
- 保留悬停、展开、选中及禁用反馈；没有修改全局 GlassLens、输入事件、发送条件或模型选择参数。
- 共五个源文件；原有未跟踪 `frontend/src/app/glass-preview-check/` 保留，未纳入提交和发布。

## 本地验证与审查

- ChatInput、HomePage、ModelSelectorPanel：90 项测试通过；ChatList.glass：1 项测试通过。
- 全量 lint 和生产构建通过。现有构建配置跳过 TypeScript 校验，本轮未声明全量类型检查通过。
- 基于原有真实首页的 DOM/CSS 静态快照，固定卡片翻转的展示终帧，比较深浅色样式。静态快照验证展示与 CSS 状态，不替代真实应用交互验收。
- 浅色常态透镜 0.18、胶囊 0.06；深色分别 0.2、0.08；悬停透镜均为 1。输入聚焦边界和拖拽虚线生效。
- 独立差异与 CSS 层叠审查未发现新增 P0/P1；输入 ref、事件、发送判断、附件可见性和模型参数保持。

## Git、CI 与发布身份

- 源提交：`4fa0fd3358111876ad2ae8aa7344c4761594a667`。
- PR [#209](https://github.com/HyxiaoGe/fusion/pull/209)，合并提交：`4ad1b1111258e59657526289c859037077b2134b`。
- PR CI [36640768015](https://github.com/HyxiaoGe/fusion/actions/runs/36640768015)：必需门禁成功；容器 UI 套件 214 个文件通过、1 个文件跳过，生产镜像构建成功。
- master CI [36641594410](https://github.com/HyxiaoGe/fusion/actions/runs/36641594410)：成功。
- dev 发布 [36641595031](https://github.com/HyxiaoGe/fusion/actions/runs/36641595031)：成功，仅 UI 部署；健康、dev browser smoke、发布台账写入步骤均成功。
- dev 台账 `current_sha` 与合并提交一致；记录时间为 2026-09-30 06:52:54（Asia/Shanghai）。
- 运行 digest：`sha256:5acb7fca8c219441dd0bed374bfd79aa1dcb18cfdda280736f029099f84ba894`。
- 运行 image ID：`sha256:c5f9e00d426b96a1d8e3389127c335c6fe34a761714ddd0b27dbc0a124bb73e2`，与台账一致，容器状态 running。
- `/chat/new` 实测 HTTP 200；根路径单独读取为正常 307 跳转。

## 原有 Chrome 页面实测

目标：[Fusion 首页](https://fusion.seanfield.org/chat/new)，官方 Chrome 扩展、原有 sean 配置与已有标签；刷新前确认没有输入草稿。

- 数据和主题加载完成后确认 sean 登录态、DeepSeek V4 Flash 模型入口和真实会话列表恢复。加载过程截图不计为稳定视觉结果。
- 深色首页常态卡片透镜为 0.2；自然指针悬停后为 1，其余卡片仍为 0.2，实际画面有清晰高光差异。
- 自然点击模板后，输入区获得预填内容并聚焦；聚焦边界为 `rgba(150, 202, 255, 0.65)`，新 composer CSS 类已加载。
- 模型面板打开后能显示当前模型、最近使用、供应商与能力标签；收起成功，没有更换当前模型。
- 验收后清空本轮预填草稿、收起面板，原标签保留优化版空输入框。未发送消息或创建模型 run。
- 浅色模式本轮仅做本地静态展示验证，真实登录态页面未切换浅色；用户反馈：“可以，效果看上去还不错”，确认保留当前优化版本。

线上画面：[优化首页](/private/tmp/fusion-glass-dev-optimized.jpg)、[模型面板](/private/tmp/fusion-glass-dev-model.jpg)。
