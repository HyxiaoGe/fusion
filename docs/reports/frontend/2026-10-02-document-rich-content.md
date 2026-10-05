# 文档富内容第一版

日期：2026-10-02，Asia/Shanghai。

用户范围：先完成文档模块第 4 项——提示块、时间线、统计卡、标签页和代码块；天气与地点卡片作为后续优化项。本轮沿用持续优化、改完直接发布的授权。未创建真实会话或发起模型生成。

## 实现

- 提示块使用语义图标、稳定实色底材及柔和边缘；标题使用正文高对比文字。时间线统一轨道、节点与间距，仅处理容器的一级列表，保留嵌套清单。
- 统计卡统一圆角、留白、数值层级与 token 底色。标签页复用已安装的 Radix primitives，具备独立 ARIA ID、循环方向键、Home/End 及 Tab 进入面板；按钮装饰子层沿用 GlassHoverLens。
- 文档专用代码块保留全文与尾部空行，独立滚动、语言标识、完整复制及成功/失败反馈。内容更新和卸载会使旧复制回调失效并清理定时器。
- 单文件 HTML 同步静态富内容样式，递归展开全部标签页，保留所有代码与表格，无复制按钮、交互 tablist 或脚本。Markdown 原文导出不变。
- 阅读正文及流式草稿没有新增 backdrop-filter 祖先；减少透明度下装饰滤镜关闭、相关底材回退实色；减少动效时关闭本轮操作控件过渡。
- 仅改文档渲染/导出及 i18n，无 API、SSE、Redux 或持久化协议改动。用户本地 glass-preview-check 与其他已有改动未提交。

## 本地验证与独立审查

- 6 份相关测试共 27 项通过，新增 9 项行为回归：键盘/关联 ID/嵌套切换、未闭合与增减标签页、长代码和 HTML 字面量、全部标签静态导出、复制成功/失败/旧异步归属。
- 目标 ESLint、git diff --check 与完整 Next.js build 通过。构建包含用户已有本地预览路由，但该目录不在提交中，发布构建由 CI 从提交独立完成。
- 独立只读审查未发现新增可达 P0/P1，审查者另运行目标 5 份测试 20 项通过。
- `/private/tmp/fusion-document-rich-fixture` 引用实际仓库组件，离线构建实际 CSS，并在后加载按钮 reset 下检查生产级联。测试浏览器无用户登录态，未启动 Fusion 本地服务。
- 浏览器覆盖浅色/深色、减少透明度/动效、自然键盘操作、逐字代码复制、真实 Blob HTML 导出、增量草稿。正文及草稿的滤镜祖先为空，侧栏和导出无横向溢出，无页面异常。主题过渡和 Radix 异步焦点需等待稳定可见状态后取证；最终测量和截图均已重新检查。
- 离线 HTML 文件：`/private/tmp/fusion-document-rich-export.html`。截图：`/private/tmp/fusion-document-rich-local-light.png`、`/private/tmp/fusion-document-rich-local-dark.png`、`/private/tmp/fusion-document-rich-local-reduce.png`。测量：`/private/tmp/fusion-document-rich-fixture/measurements.json`。

## 发布与真实验收

- 实现提交：`c63dc48980a8d704bed45df56bab183532c87b01`，PR：https://github.com/HyxiaoGe/fusion/pull/233 。
- PR CI [36972429377](https://github.com/HyxiaoGe/fusion/actions/runs/36972429377) 必需检查全部成功；全量 2,672 项通过、35 项跳过，222 份测试文件通过、1 份跳过，生产镜像构建成功。合并版本 `f374ff2a3fdaad0d8ad1596a045ec212fb88b695`，合并时间 14:20:57。
- master CI [36973117202](https://github.com/HyxiaoGe/fusion/actions/runs/36973117202) 和 dev 发布 [36973117744](https://github.com/HyxiaoGe/fusion/actions/runs/36973117744) 均成功；API 与能力路由评估按本轮 UI 影响正常跳过。dev 健康检查和 browser smoke 成功。
- 发布前复用官方 Chrome 扩展 browser 1、既有 Fusion 标签 643086762。已有清单会话 `abc3cc76-edc0-4a76-97d8-70b22a935f9d` 的文档 v3 含 4 个提示块、4 个统计卡、1 个时间线、1 个代码块，没有内容标签页。原杭州会话 `8fdaebbf-890f-404c-9f25-807f7008cd17` 保留，可用于长文与版本验收。
- 真实新文档生成和流式性能未测；已有文档无内容标签页，标签页由离线实际组件和回归测试覆盖。深色模式为离线实测，未更改真实页面主题设置。
- 已发布版本原登录态浅色实测：5,137 字清单的提示块、4 个统计卡及 6 节点时间线可见，正文无模糊祖先、侧栏无横向溢出，页面无记录到的 error。
- 22 行周报代码原文与发布前一致（191 字符、末尾换行完整），新工具栏可见。复制按钮显示“已复制”；工具的浏览器会话剪贴板读取返回空字符串，故不能确认真实系统剪贴板字节。离线逐字复制与复制失败路径通过。代码区域聚焦后 End 滚到末尾（scrollTop 60.75、clientHeight 448、scrollHeight 509）。
- 真实 v3→v1→v3 切换：Day 3 在“参加部门新人培训”和“深入岗位职责”之间正确变化；v3 新增午餐条目恢复，代码块仍完整。Escape 关闭成功。
- 临时媒体偏好实测：提示底色 `oklch(0.97 0.025 152)`、统计卡/代码头部/复制按钮渐变 none、复制按钮底色 `oklch(1 0 0)`、过渡 none，装饰 lens display/filter 均 none。媒体偏好已清除。
- 最终恢复原杭州会话、浅色模式、自动执行模式、mimo-v2.6-pro 与空输入框，文档面板关闭。未创建真实会话、调用模型、修改文档内容或同步 Feishu。
- 线上 HTML 文件落地及内容未重新核验；本轮已有实际组件导出 Blob、离线文件与完整静态页检查，不声称真实导出文件内容通过。
- 实页截图：`/private/tmp/fusion-document-rich-live-light.png`、`/private/tmp/fusion-document-rich-live-timeline.png`、`/private/tmp/fusion-document-rich-live-code-end.png`。减少透明度测量：`/private/tmp/fusion-document-rich-live-reduce-measure.json`。

## 实际运行身份

- accepted 时间：14:26:16（Asia/Shanghai）。SSH 只读核对 UI 台账与容器，SHA `f374ff2a3fdaad0d8ad1596a045ec212fb88b695`，容器 `running`，ref/image ID 一致（identity_matches=true）。
- digest：`sha256:a6b2e87582100af80be7ee088319483a411b34600c76e5a2efa42541f7dff197`。
- image ID：`sha256:0eea7f3e2e8071e224ad8c50e43e81b00914ef08c7fc5b5da40006241cd6f7fd`。
- 原始运行证据：`/private/tmp/fusion-document-rich-runtime.json`；部署步骤日志：`/private/tmp/fusion-document-rich-deploy-ui.log`。

本报告与执行台账在本地追加保留，未将既有无关文档或预览目录带入 UI 发布。
