# 文档阅读细节补齐

记录时间：2026-10-03T17:35:57+08:00

## 本次范围

用户接受 Codex 负责前端四项修复，Claude Code 负责后端 runtime_prompts.toml 的短数值指引及真实登录态复验。沿用会话中的 dev 发布授权，不涉及生产部署。本轮前端基线为 b0d70c9ad10fa763495aaf52486fcb9575149f0f，已包含独立合入的后端提示词 PR #253；未修改后端。

工作分支 codex/document-reading-polish，独立管理工作树 /Users/sean/.codex/worktrees/document-reading-polish/fusion。前端提交 7db48396927adc8f5e1de7c1e9abf8877c37c837，PR [#254](https://github.com/HyxiaoGe/fusion/pull/254)。主目录已有台账和报告不纳入本轮提交。

## 行为与实现（代码阅读）

- 数字卡片仅按 Unicode 字符数判断，超过 20 个字符改为 15px/400 字重/1.7 行高；短值保持原样，完整文本和差异标记保留。HTML 导出采用相同样式。
- 页面、Markdown 和 HTML 共用结构化 provider 映射。amap 及历史结构化“高德地图”值走 i18n：中文高德地图、英文 AMap；内部或未知标识隐藏。不会扫描正文或来源标题，来源标题、网址、查询时间和品牌原文不变。与后端隐藏内部服务名称而保留品牌实体的规则一致。
- 版本和比较控件预留静态槽位及非焦点占位，正文先返回时可阅读和导出；多版本、单版本、请求失败和切换文档保持原有语义。
- GFM 清单使用主题对应的原生复选框 color-scheme，保持 disabled，只读、checked 状态和 HTML 导出结构不变。

## 本地验证（实测）

- 文档相关 13 个文件 112 项测试通过，完整 ESLint、生产构建、git diff --check 通过。
- 长值与服务商新回归先在旧行为失败，再随修复通过。工具栏覆盖详情/正文不同返回顺序、切换文档、失败及单版本。
- 独立审查未发现本次新增的可达 P0/P1；主代理核对实际差异。
- 独立 tsc --noEmit --pretty false --incremental false 与基线 b0d70c9a 对照：两者均 39 条诊断，日志逐字一致。完整类型检查仍不通过，不能记作通过。
- 离线 Chromium 从真实组件生成 HTML 和当前 CSS，不启动 Fusion 本地服务，不访问远端。880/601/600/390px 下加载前后比较槽位和 Markdown 导出按钮坐标/尺寸差值小于 0.5px，未发生横向溢出；长值计算字号 15px/400，短值 18px/600。深浅色 disabled 清单分别为 dark/light；HTML 导出长值样式一致。截图人工检查通过。
- 离线初版夹具遗漏 GlassLens 共享样式导致比较按钮宽度误差 6px；补齐夹具后稳定，无需更改产品代码。临时夹具的 CSS 类映射同时修正全局 dark 和数字小数选择误替换。临时测试已移除，未提交 mock 页面/预览。
- 证据暂存 /private/tmp/fusion-document-reading-polish-20261003，含视觉夹具/脚本/结果/四张截图及基线与当前类型日志。

## 发布状态

PR CI [37113505871](https://github.com/HyxiaoGe/fusion/actions/runs/37113505871) 全部必需检查成功，API 正常跳过；Docker 前端 2840 项通过、35 项平台专用跳过。PR 于 2026-10-03 17:42:28（Asia/Shanghai）合并为 d44c00592511812d7e9d3d97a96891aa40c0bbef。master CI [37113956241](https://github.com/HyxiaoGe/fusion/actions/runs/37113956241) 全部必需检查成功，API 正常跳过。dev [37113956473](https://github.com/HyxiaoGe/fusion/actions/runs/37113956473) 成功，API 与 routing eval 正常跳过；Windows 前端 2840 项通过、35 项平台专用跳过。候选健康与发布 browser smoke 成功，回滚步骤未触发。

## 真实页面缺口

本轮官方 Chrome 扩展未出现在可用浏览器列表，同一 Chrome 定位复核一次后仍缺失。未新开替代浏览器、标签、配置或复制登录态。以上离线检查不能替代真实登录页面验收；按用户接受的分工留给 Claude Code 复验。

### dev 运行身份核对（实测）

- accepted：2026-10-03T17:48:22+08:00（Asia/Shanghai）。
- UI SHA：d44c00592511812d7e9d3d97a96891aa40c0bbef，发布 run：37113956473
- digest：sha256:d075b730bcbd173047559d6e70008f1a50f5b0c961576766ac4f6c780e3e4c59
- image ID：sha256:84bf8253e28e434b9e65ab174e93155f50e301e8f0f3422077ec0cb32d6b775c
- 容器 running，运行摘要引用和内容 ID 与台账逐项相同。API 仍为独立提示词提交 b0d70c9a，SHA、摘要引用与内容 ID 均与本轮发布前一致。
- 实际打包资源中长值样式、版本占位、服务商翻译 key、深色 checkbox 样式均存在；只读资源检查不能替代用户操作验收。
- 健康契约首页按现有路由返回 307，跟随重定向后为 200，与发布 fetch 检查相同。真实登录态验收缺口不变。

## 收尾与保留项

只提交本轮 12 个前端文件，报告和台账留在主目录本地，已有文件和无关改动保留。临时视觉测试已移除，未新增预览路由或 mock 数据。类型基线临时源码副本已清理，证据日志保留。

独立工作树已无未提交改动，但应用归档工具返回 “This worktree is protected by a pinned task or workspace.”，因此保留 /Users/sean/.codex/worktrees/document-reading-polish/fusion 及其本地/远端 codex/document-reading-polish 分支，没有绕过置顶保护。此项不影响已完成的 dev 发布。
