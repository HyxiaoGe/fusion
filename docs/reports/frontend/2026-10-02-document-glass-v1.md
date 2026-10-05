# 文档组件 Liquid Glass 第一版

- 本批基线：`a3e60e55abc7334ede71e683d406972b46d2bc61`；代码提交 `946904847cf4e5d91fa9becf30a6c7041d0c31a2`，PR [#231](https://github.com/HyxiaoGe/fusion/pull/231)。
- 授权来自本会话持续 UI 优化与改完直接发布；Claude Code 交接仅作为待核验参考。没有提交 `glass-preview-check/`、此前本地报告、旅行文件或其他无关改动。

## 变化

- 完成文档卡片沿用共享 GlassHoverLens 的鼠标光效，统一图标、边缘与焦点；最大宽度从 36rem 调整为 42rem。
- 草稿卡片与预览同宽，保持实色渐变/边缘高光，没有加入 blur 或装饰滤镜。文档实时数据、延迟 Markdown 渲染和用户阅读时不抢滚动的逻辑保持。
- 面板标题、版本与导出从拥挤单行调整为标题/工具栏两行。标题可以折行，单版本显示 v1 标记，多版本保留原生 select 的键盘交互。正文仍是实色，和工具栏互为兄弟层；没有给面板父层添加滤镜。
- 为现有 9 个 Glass CSS module 与 TabsList 补充 `prefers-reduced-transparency: reduce`：实色 token、停止 backdrop-filter、隐藏装饰光层，保留主 GlassLens 的选中语义、边框和 focus。其他历史图片查看器等 Tailwind 半透明覆盖未扩展到本批。
- 使用现有 token/i18n，没有改 SSE、Redux、持久化或后端。

## 本地证据

- 文档测试 12 项，辅助/知识库测试 25 项，侧栏/登录/模型/输入测试 117 项，共 154 项通过；目标 TSX ESLint、diff 检查和最终生产构建通过。生产构建不等于全仓 TypeScript 检查；本地构建包含用户现有未提交预览路由，CI 从提交树单独构建。
- `/private/tmp/fusion-document-glass-fixture/` 是离线自动化样本：实际组件和 CSS，API 数据只在此样本替换，不运行 Fusion 本地服务。检查浅色、深色、草稿追加、v2→v1、Escape、媒体偏好模拟。所有场景正文/草稿的 computed backdrop-filter 祖先链为空；减少透明度时 TabsList 与 lens 无模糊；面板无横向溢出；浏览器无 pageerror。
- 截图：`/private/tmp/fusion-document-glass-local-card-light.png`、`/private/tmp/fusion-document-glass-local-panel-light.png`、`/private/tmp/fusion-document-glass-local-panel-dark.png`、`/private/tmp/fusion-document-glass-local-reduce.png`；测量 `/private/tmp/fusion-document-glass-fixture/measurements.json`。
- 独立作者外代码阅读未发现 documents/tabs 可达 P0/P1。共享 CSS 的子代理审查属于作者自查，主代理另核对了实际 diff 与媒体偏好行为。
- [MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-transparency) 标明此媒体特性仍有浏览器兼容限制；本轮自动化模拟不等于所有浏览器都能读取系统偏好。

## 原登录态与发布

- 改版前已通过官方 Chrome 扩展复用既有 Fusion 标签 `https://fusion.seanfield.org/chat/8fdaebbf-890f-404c-9f25-807f7008cd17`，浅色，真实文档 v1/v2 为 4,021/4,089 字。基线截图 `/private/tmp/fusion-document-glass-before.png`；没有修改这些文档。
- 交接中“浅色从未真实验收”不适用于本会话：9 月 30 日原登录态浅色聊天辅助界面已验收，证据见 `2026-09-30-body-support-v2.md`。此前验收未覆盖新增文档模块，本批补齐。
- 发布前只读 SSH 核对 dev UI 仍是 PR #228 的 `ac10d66b12b4ab819c1b126756cb1cb5423b5403`，容器 running，台账 ref/image ID 一致；此前仅后端发布没有更新此 UI。
- PR CI [36963346985](https://github.com/HyxiaoGe/fusion/actions/runs/36963346985) 与必需门禁成功：220 个测试文件通过/1 个跳过，2,663 项通过/35 项跳过，Docker 生产构建通过。
- PR #231 于 12:17:44（Asia/Shanghai）合入 master，merge SHA `2f7894068255312e5c2b374b98eac0663e92ccee`。master CI [36963928733](https://github.com/HyxiaoGe/fusion/actions/runs/36963928733)、dev [36963929131](https://github.com/HyxiaoGe/fusion/actions/runs/36963929131) 均成功。
- 长文生成实测等待新增测试会话/模型调用授权，当前没有触发真实生成。此前持续优化/发布授权不单独推导新增模型调用，第三方交接建议作为参考。

## 第一轮真实验收与补充修复

- 首轮 master CI 与 dev 均成功，Windows 镜像内 2,663 项通过/35 项跳过；UI 于 12:23:45（Asia/Shanghai）accepted。SSH 核对 SHA `2f7894068255312e5c2b374b98eac0663e92ccee`，running、ref/image ID 与台账一致。digest `sha256:77e487b6cbbc0c19be82a376984041b79bc4c21e40ca5cbc639bec24482058d4`，image ID `sha256:35c4f525d8c5928f07b830bd6391300e19eab7c582a65bfd917f181fb023cc8c`。
- 原登录态浅色面板标题/工具栏分行，加载中导出禁用、加载完成恢复可用。正文实色、无模糊祖先；v2→v1→v2 内容正确切换：v1 首日下午为灵隐，v2 为雷峰塔+河坊街。选择框焦点轮廓可见；键盘事件未获工具确认版本改变，不把 selectOption 验证写成键盘切换通过。
- 原标签在前台时，实际正文 scrollTop 3,087，header top 仍为 0，正文独立滚动。此前背景标签的原生滚动未产生正文位移，不记为通过。
- Markdown 下载事件等待超时；原生 Chrome 下载列表明确显示同名 v2(1).md、12.4KB、Done。HTML 入口已点击、页面未报错，文件落地尚未确认。Downloads 目录在普通/升级 shell 均受 macOS 权限限制，没有核验新下载内容或修改系统权限。
- 真实减少透明度模拟生效，装饰 lens 无 blur，但 card 计算背景为 transparent/none、TabsList 仍是 blur(4px)。首轮降级验收未通过；本地 Vite 样本的 CSS 顺序没有暴露生产覆盖，模拟已恢复。
- 补充 PR [#232](https://github.com/HyxiaoGe/fusion/pull/232) 只提高 card/actionButton 底色选择器优先级，并确保 solidFallback 的 filter none 优先于 Tailwind utility；减少动画的 selector 同步保持优先级。12 文档回归、生产构建以及追加后加载按钮重置/blur utility 的离线五场景通过，独立复审未发现可达 P0/P1。补充发布后的稳定状态结果见下节。

## 补丁交付

- 最终补丁提交 `a3b08116804055bf42180c846f3747f7ac87b241`，PR CI [36965399999](https://github.com/HyxiaoGe/fusion/actions/runs/36965399999) 与 required gate 成功；2,663 项通过/35 项跳过，Docker 生产构建通过。旧提交的运行因新推送被取消，不用于最终验收。
- PR #232 于 12:46:12（Asia/Shanghai）合入 master，merge SHA `187fc28d1effae24ccc367cc716a8676fd71eed9`。master CI [36965999879](https://github.com/HyxiaoGe/fusion/actions/runs/36965999879)、dev [36966000382](https://github.com/HyxiaoGe/fusion/actions/runs/36966000382) 均成功；master 与 Windows 镜像内全量测试均为 2,663 项通过/35 项跳过，API 与 routing eval 正常跳过。
- 最终离线样本五个场景通过；增加同时减少透明度/减少动画的检查，导出按钮 transition-property 为 none。全局 CSS 的 transition-duration 为 0.01ms，样本最初强断言 0s 失败后，按有效 property 修正检查；没有据此修改生产全局规则。
- UI 于 12:51:31（Asia/Shanghai）accepted，部署健康和 browser smoke 通过。只读 SSH 两次核对 running、SHA、ref/image ID 与发布台账一致。digest `sha256:0d9f3d244118685c590a315d81bcc3d9f47a89626dcefd6e91e083513af7a69c`，image ID `sha256:9962c817082dedbf3abd2c8a398de7f13f7ba6e44e1c868bcaedfd27d3830089`。

## 最终原登录态验收

- 官方 Chrome 扩展复用同一 Fusion 标签并刷新。浅色卡片和两个导出按钮的 computed background-image 都恢复预期渐变；面板正文为实色、无 backdrop-filter 祖先、无横向溢出，现有 4,089 字文档加载正确。
- 同时模拟减少透明度和减少动画：卡片与导出按钮为不透明实色、渐变为 none；TabsList 的 backdrop-filter 为 none，六个装饰 lens 全部 display none/filter none；卡片与导出按钮 transition-property 为 none。
- 模拟切换后的首次即时读数与后续稳定状态不一致，TabsList 一度仍读到 blur(4px)。随后通过当前标签的 `CSS.getMatchedStylesForNode` 直接确认线上媒体规则包含 `backdrop-filter:none!important`，取得最新页面状态后再次读取为 none；最终结论使用稳定状态证据，没有为这一即时读数追加修改。
- 发布后再次实测 v2→v1→v2：v1 首日环湖+灵隐，v2 首日环湖+雷峰塔+河坊街，内容与所选版本匹配。Escape 关闭面板后焦点返回 v2 卡片。临时媒体偏好清除、空草稿/空知识库选择/自动模式保持，原前台飞书标签已恢复。
- 实页截图：`/private/tmp/fusion-document-glass-live-final-light.png`、`/private/tmp/fusion-document-glass-live-final-cards.png`、`/private/tmp/fusion-document-glass-live-final-reduce.png`；最终降级测量 `/private/tmp/fusion-document-glass-live-final-reduce.json`。发布/CI 日志为 `/private/tmp/fusion-document-glass-cascade-*-final.log`，运行身份为 `/private/tmp/fusion-document-glass-cascade-runtime.json`。
- 真实长文流式生成未执行：新增测试会话和模型调用授权问题尚未收到答复。草稿增量与滤镜祖先仅有离线实际组件/代码阅读证据，低端设备帧率未测；深色仅离线检查。HTML 文件落地、导出文件内容和原生选择框键盘版本切换仍保留上文缺口。没有把这些写成真实通过。
- 执行台账与本报告在本地补记；既有本地报告、预览与旅行文件未提交，未因补记再触发发布。
