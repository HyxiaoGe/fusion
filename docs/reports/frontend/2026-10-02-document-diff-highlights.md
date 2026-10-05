# 文档差异细节优化

2026-10-02，时间均为 Asia/Shanghai。

用户反馈：移除未变内容入口，新增/删除/修改分别着色，变化区域需要明显分隔。基线 bcdf003e7adadf41649aa0be37bbc9e2dff29563。

## 实现

只展示变化块；未变表格行列和列表项保留在变化块内，便于定位。新增绿、删除红、修改蓝。Day 3 按单元格标记三处实际变化，新增午餐只标记新增条目。独立分隔线、编号及类型图标帮助区分变化。

复用现有只读快照和有预算的 LCS。原文 UTF-16 范围投影到已解析 Markdown，不插入原始 HTML，不修改保存内容。处理 CRLF、重复正文、同名容器标题、转义文本、代码原文。统计卡和容器标题的细粒度高亮暂未扩展，仍可在原文差异查看。未改后端、版本归属、普通阅读位置与导出。

## 本地验证

- 目标 59 项通过；全量 231 文件、2,800 项通过。
- Next 构建、目标 ESLint、diff check 通过。
- TypeScript 当前 39 项与既有基线逐条相同，本轮无新增。
- 独立只读审查未见 P0/P1；同名容器标题的正文投影问题修复并回归。
- 基于实际 token 计算：浅/深色类型徽章对比度最低 6.14，文字高亮最低 13.14。

本地日志：/private/tmp/fusion-diff-highlights-final-target.log、final-full.log、final-build.log、lint.log、types.log。

## 发布与真实页面

- 实现提交：012f40767976ff40ec1d08916ba0aacc58cb6eb8。
- PR #244：https://github.com/HyxiaoGe/fusion/pull/244。
- PR CI 37016509863 全部 required checks 成功。
- 22:05:01 合入 master：696c03751448a2227a30db3151e0dd226fe9fc1d。
- master CI 37017392856、dev 发布 37017393884 全部成功。
- 验收复用原 Chrome 标签，现有文档 v3 阅读位置 4191.75，浅色、空输入框；不会创建会话或调用模型。

- 22:10:59 accepted；运行 current_sha 696c03751448a2227a30db3151e0dd226fe9fc1d，容器 running。
- digest：sha256:cc4e280d9a1055d8816a27f45a23a011e882e4491a6b912e13c2a3f14e5ab0b1。
- image ID：sha256:321d30335bbc3ee905f67267d6e87904f18ab801a94b768d7e9163a91723d63e。运行 ID/ref 与发布台账一致。
- Windows 230 文件、2765 项通过，35 项正常跳过；候选健康与 dev browser smoke 成功，API 跳过。

## 原登录态实测

- 原 Chrome 标签及已有会话 https://fusion.seanfield.org/chat/abc3cc76-edc0-4a76-97d8-70b22a935f9d，未建会话、调用模型或修改文档。
- v2→v3 一处变化：只有午餐新增条目标绿，原三条未误标；未变正文入口不存在。
- v1→v3 十一个变化块：前后表格各三格修改标蓝，Day 3 日期列、表头与其他天数没有高亮；真实文档中两条删除项标红，新增项绿色。十条分隔线区分十一个变化块。
- Enter 导航聚焦变化 2，逐一点击到末尾 11/11 保持计数和禁用状态；手动滚至文末后也为 11/11。
- 返回正文仍恢复 4191.75，刷新及重开后该阅读位置保持。
- 浅/深色实际截图通过，减少透明度/动效下阅读区域 backdrop-filter 为 none，高亮保留。实际蓝色单元格文字对比度 15.58/15.55，绿色文字高亮 15.13/13.14。
- 清除临时媒体覆盖，恢复原浅色、空草稿、自动模式和原尺寸，已有清单 v3 对比 v1、变化 1/11 留作查看。未使用新浏览器、标签或 profile。

截图：
- /Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/document-diff-highlights-light.png
- /Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/document-diff-highlights-dark.png

仅十一项任务前端文件合入；原文档/预览/旅行文件保留，发布后报告与台账补记留本地。统计卡及容器标签仍以原文差异查看，真实触摸设备未覆盖。
