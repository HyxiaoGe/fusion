# 正文辅助界面与消息展示第二批

日期：2026-09-30（Asia/Shanghai）。用户接受正文第一版后要求“OK，没啥问题继续吧”，沿本轮持续优化、直接发布 dev 的授权实施。

## 改动

- 推理入口、活动信息和轨迹状态降低整体边框重量；推理正文采用 13px/1.7 行距、独立段落与列表层次，保留 280px 阅读滚动区，轨迹详情入口 28px。
- 推理按钮增加 aria-expanded 与稳定 aria-controls；折叠内容通过 inert/aria-hidden 退出键盘导航与辅助阅读，内容节点继续保留，流式期间保持展开。
- 回答依据收敛为轻信息条；来源卡片、全部入口与侧栏使用统一的边缘高光、稳定阅读底色和焦点反馈。卡片宽 176px/间距 8px、侧栏 440px，索引、来源分组、关闭焦点和重复定位沿原实现。
- 用户消息普通气泡最大 min(72%,42rem)，文字 15px/1.7、pre-wrap/overflow-wrap:anywhere；纯文本、编辑、附件和失败路径保持。
- 复制、编辑和重试控件统一为 32px；桌面操作区自身 focus-within 显现，用户侧只传间距，不再覆盖可见性。
- 17 个前端源/样式/测试文件，没有新依赖；未改变发送、SSE、Redux、持久化、来源模型或布局算法。

## 本地验证

- 新推理语义用例在旧实现失败（按钮缺少展开状态），修复后通过；验证折叠辅助内容不可读、inert 属性、展开后同一内容节点和稳定 controls。
- 16 个目标文件共 178 项回归通过（主组件/推理/状态/轨迹/来源/消息/滚动/弹层协调）；目标 ESLint、生产构建与 diff 检查通过。构建按仓库配置跳过类型检查，不宣称全量类型检查通过。
- 独立只读审查未发现新增可达 P0/P1，复核了推理 Hook 顺序/流式节点、来源索引/重复定位/焦点、桌面操作键盘可达、用户编辑回调及耗时更新。
- 发布前原登录态页面实际键盘基线：在原用户消息容器按 Tab，当前焦点为“编辑”，其操作栏 computed opacity 为 0、pointer-events 为 none，确认旧版按钮聚焦时不可见；随后返回聊天页签，未编辑或发送消息。
- 原会话真实推理文字提取后使用实际 ReactSSR 与 CSS 离线检查深浅色：折叠内容高度 0px/不可交互，展开 293px；头部 38px，轨迹入口 28px；没有整页横向溢出。两个独立 SSR 样本组装时分别赋予唯一控件 ID，避免离线页面重复 ID 影响测量；产品实例的 useId 语义由回归验证。
- 来源与消息控件离线样本使用现有测试 fixture。深浅色下来源卡片 176px/间距 8px、侧栏 440px；全部入口/关闭按钮焦点轮廓 2px，高亮/失败/部分可用色调随语义 token 切换。消息控件 32px，Tab 聚焦使 opacity 0→1、pointer-events auto；气泡 672px、内容 638px，无换行/长串溢出。
- 图片与测量：`/private/tmp/fusion-body-v2-aux-{light,dark}.png`、`/private/tmp/fusion-body-v2-aux-metrics.json`；`/private/tmp/fusion-evidence-v2-{light,dark}-{entry,sidebar}.png`、`/private/tmp/fusion-evidence-v2-metrics.json`；`/private/tmp/fusion-message-controls-{light,dark}.png`。
- 没有启动本地服务；临时导出源已删除，用户现有预览目录与本地历史报告保留。上述展示是离线证据，真实开合/焦点/引用在发布后另验。

## 发布与原登录态

- 源提交 `8a6c31a4eaf93e73ddddee5e29fa4858e1e0a404`，PR [#215](https://github.com/HyxiaoGe/fusion/pull/215)，PR CI [36674982488](https://github.com/HyxiaoGe/fusion/actions/runs/36674982488) 成功（全量前端 lint、216 个测试文件通过/1 个跳过，2,643 项通过/35 项跳过，Docker 生产构建成功）。PR 合并为 `975b46486bd9574c2ec486e139b2973cee41b85f`；master CI [36675644397](https://github.com/HyxiaoGe/fusion/actions/runs/36675644397) 成功。
- dev 发布 [36675644856](https://github.com/HyxiaoGe/fusion/actions/runs/36675644856) 首次和重跑均在 Windows runner 镜像内测试阶段失败：既有 `HomePage.test.tsx` 的“十二条后端任务手动轮换到出行页，并从两个入口只预填内容”超过 5 秒时限，其余 2,642 项通过/35 项跳过，服务器部署未执行。该文件不在原 UI 差异；同一 master 的 CI 全量通过，本地额外复核该文件 13 项通过（2.71 秒）。原始日志 `/private/tmp/fusion-body-v2-deploy-failure.log`、`/private/tmp/fusion-body-v2-deploy-rerun-failure.log`。日志只能确认超时用例；所有用例整体变慢支持 runner 负载放大开销的推断，未定位具体超时语句。
- 定位回归自身开销：带 name 的角色查询为所有候选计算 accessible name，异步假计时每个 timer 后经过真实 setTimeout yield；该页面的两级轮换 timer 仅同步 setState。补充 PR [#216](https://github.com/HyxiaoGe/fusion/pull/216)、提交 `bd00bee10afb03016c2b668799c9b6e990892d2e` 只优化此用例的稳定控件复用、卡片/弹窗内语义查询与同步 act 计时，保留 700ms、完整内容回调、名称/图标颜色、返回起点和消失断言，并显式检查模板选择后关闭弹窗。产品代码与 5 秒时限保持。13 项测试、目标 lint/diff 检查通过，本地单次前后 434ms→200ms（非性能保证）；日志 `/private/tmp/fusion-body-v2-home-{before,final}.log`。
- 补充 PR #216 独立最终审查未发现 P0/P1；PR CI [36677521049](https://github.com/HyxiaoGe/fusion/actions/runs/36677521049) 与必需门禁成功（216 个测试文件通过/1 个跳过，2,643 项通过/35 项跳过，Docker 生产构建成功），合并为 `16cff0c72f7f54a220c2b64b81719ec8127d6269`（14:25:49 Asia/Shanghai）。最终 master CI [36678179963](https://github.com/HyxiaoGe/fusion/actions/runs/36678179963)、UI dev [36678180586](https://github.com/HyxiaoGe/fusion/actions/runs/36678180586) 均成功。UI 的 17 文件内容仍来自 #215，#216 只改变测试。
- UI 于 14:32:38 accepted（Asia/Shanghai），健康与 browser smoke 通过；API 正常跳过。只读 SSH 核对 current SHA 为 `16cff0c72f7f54a220c2b64b81719ec8127d6269`，fusion-ui 容器 running，ref/digest 与台账一致：`sha256:87734a0bbbdd35fe4c093b571ee6d5f82f4da930995e58f37543bdc2bd4365c3`；image ID `sha256:760d3e26cdce05590e47ea73a4ecfb09a1ab9cad6f39d8fe8fb49803f84d6255`。
- 最终 Windows runner 镜像内测试 216 个文件通过/1 个跳过，2,643 项通过/35 项跳过；原超时用例本次 2,140ms 通过，仍使用 5 秒时限。日志 `/private/tmp/fusion-body-v2-final-publish.log`；这证明本次发布门禁通过，不等于保证未来所有 runner 负载下的耗时。

## 原登录态页面实测

- 复用既有 Chrome 扩展标签；刷新原会话 `c7301b0c-7d06-4b77-951a-32677988a230` 后，新推理 aria-controls 已加载，空草稿保持。没有新建浏览器/标签、本地服务、真实生成或外部来源跳转。
- 在原用户消息容器按 Tab，焦点为“编辑”；操作栏的 opacity 经过 120ms 过渡后为 1、pointer-events auto，控件约 32px。Enter 进入编辑展示原文，Escape 取消后原消息保留。助手“复制”键盘聚焦同样显示操作栏；没有触发复制/重新发送/重新生成。
- 推理折叠时同一 controls 为 `:r1n:`、高度 0、inert 属性存在、aria-hidden=true；Enter 展开后高度约 293px、inert 移除、aria-hidden=false，真实 6,518 字符推理内容的滚动区 280px、13px/22.1px，scrollHeight 1,952px。再按 Enter 恢复折叠，controls 保持；随后 Tab 到“查看轨迹”（高度约 28px）。
- 既有六列表格会话 `784af61a-97be-4c17-b0f6-d39f7f7b6f31` 的来源入口展示 6 张卡，实际宽约 176px。表格“552B MoE”参数单元格中的引用 42 打开 440px 侧栏，`data-highlighted=true` 的来源标题为“DeepSeek V4.1 Flash：更强、更快、更普惠”；自动滚动结束后其 top/bottom 为 515.55/577.51px，位于 1,029px 视窗内。Escape 关闭后 dialog 消失，焦点回到该引用按钮。
- “查看全部依据”使用 Enter 打开，已使用 41/候选 47/深读 2 的分组与既有内容保留；Escape 关闭后焦点回到入口。来源中的模型事实仅用于既有页面渲染与映射验证，本轮没有核查这些外部事实。
- 已回到原机器人会话文章开头，主阅读区 scrollTop=0，推理折叠，弹层已关闭；空草稿、空知识库选择和自动模式保持原状。
- 真实截图：`/private/tmp/fusion-body-v2-live.jpg`、`/private/tmp/fusion-body-v2-reasoning-live.jpg`、`/private/tmp/fusion-body-v2-citation-live.jpg`。本次真实页面是浅色；深色、长串/多行用户文字及生成中状态仅由本地样本与回归覆盖，未宣称这些路径有原登录态实测。
- 发布后报告和执行台账补记保存在本地，避免额外触发共享部署；用户现有预览目录与历史本地记录保留。
