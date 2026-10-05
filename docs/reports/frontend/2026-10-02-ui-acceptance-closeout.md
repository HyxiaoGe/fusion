# UI 优化收尾验收

日期：2026-10-02，Asia/Shanghai。

用户在确认已交付范围后明确要求「可以，补齐吧」，本轮授权涵盖必要的新测试会话和模型生成。复用官方 Chrome 扩展、原登录态和既有 Fusion 标签；不启动本地服务、不替换浏览器配置、不修改外部文档或删除数据。

## 运行版本

- 本地 HEAD 与 dev UI 台账 SHA 均为 `5276f28919bd314296322b116cb17c0cfd15dddd`。
- 运行镜像 ID 为 `sha256:e3fe6377b8054a8db3c783872929db901aaa9a26da17dd925b4a56229fddf3d3`，容器 running，与台账一致。
- digest 为 `sha256:cf80a8b1391d259bac82276b7ba1c4ef01b922164f22be9aae94e7c026612462`；本轮没有代码修改或新发布。

## 已有文档：深色、版本与复制

- 复用「新员工首周清单」会话 `abc3cc76-edc0-4a76-97d8-70b22a935f9d`，打开 5,137 字 v3。深色标题、工具栏、提示块、统计卡、表格与正文均有真实页面证据，侧栏无横向溢出。截图 `/private/tmp/fusion-acceptance-document-dark.png`。
- 原生 select 的菜单位于 Chrome 原生界面；标签级 DOM 按键不能完成选择。仅此原生菜单步骤使用同一 Chrome 原生控件和方向键/Return，然后回到原标签读取结果。v3→v2→v1→v3：v2 的 Day 3 为部门新人培训、没有午餐条目；v1 的 Day 3 为深入岗位职责、没有午餐条目；v3 恢复培训与午餐。未用 selectOption 冒充键盘验收。
- 文档代码先从实际 DOM 取得完整 191 字符原文，点击复制，关闭侧栏，在空输入中使用原生 Chrome 系统粘贴；191 字符逐字相等，末尾换行保持。随后清空草稿，没有发送代码。
- 聊天 CodeBlock 在新测试会话的真实 Python 示例中展开、复制，浏览器会话剪贴板和输入框粘贴均为完整 1013 字符、逐字相等。原生系统粘贴未取得文本，因此这条证据是会话剪贴板与页面粘贴，不能将其表述为物理系统剪贴板字节核验。测试后清空输入并恢复折叠。

## 真实导出文件

- 在原登录态 v3 页面点击 HTML 与 Markdown。扩展下载事件等待 6 秒超时；Finder 原生目录显示本轮两份实际文件，新增时间均为 15:59。
- 仅复制这两份下载文件到 `/private/tmp/fusion-ui-acceptance-20261002/`，保留原文件和其他下载。未改变下载目录、系统权限或访问凭据。
- Markdown 11,727 bytes，HTML 30,427 bytes；均包含部门新人培训与新增午餐内容。两个文件中的代码均为 191 字符且逐字一致。HTML 为带 doctype 的完整静态文件，无 script，包含 callout、stats 和 timeline 标记。
- 文件：`新员工入职第一周清单（按天执行版）-v3.md`、同名 `.html`；检测结果 `existing-export-check.json`。HTML SHA256 为 `d90ff042216771d22b5f6116a2bf3066e67e25907013adc746d0594c39272451`。

## 新长文生成与性能观察

- 新会话 `3fce3479-4ae6-4e1f-8c10-84be4aab950f`，16:00:28 开始。自然任务：为虚构 8 人研发团队生成约 6000 字四周手册，含统计卡、时间线、提示、24 项验收表、前后端专项标签页与约 40 行 Python 示例；明确无需联网或同步外部平台，没有强制模型工具输出。
- 直接观察草稿从 692、1044、1158 字等状态增量增长；预览内容 DOM 文本从 922→1436→2635→5923 字符。草稿及父层无 backdrop-filter。
- 使用标签级滚轮在草稿内上滚至 scrollTop=0；内容从 1085 增长至 1436 字符时仍为 0，没有抢滚动位置。下滚至底部后，随新增内容恢复跟随，scrollTop 随 scrollHeight 同步增长。
- 首次四倍 CPU 降速模拟持续 68.35 秒：浏览器累计 ScriptDuration 增加 5.47 秒、LayoutDuration 0.42 秒、RecalcStyleDuration 0.58 秒、TaskDuration 15.76 秒。期间滚轮可操作；这些是浏览器计数，不等同 FPS 或低端真机结论。该阶段后已恢复倍率 1。
- 后续在包含 25 个表格行（含表头）、两内容标签页和约 23,059 字符聊天 DOM 文本的页面继续观察四倍 CPU 模拟。真实草稿中的 ArrowRight 切至后端、Home 回到前端，ARIA 关联与选中状态正确。该阶段持续 82.87 秒：ScriptDuration 增加 6.81 秒、LayoutDuration 0.49 秒、RecalcStyleDuration 0.72 秒、TaskDuration 22.14 秒；结束后恢复倍率 1。
- 实际生成耗时 713.27 秒，16:12:24 的完成消息交付 11,568 字 v1，草稿被正式文档卡片替换。自动选择实际使用 mimo-v2.6-pro；生成耗时属于本次完整模型任务，不作为 UI 帧率指标。
- 正式文档含 24 个任务数据行、4 个统计卡、四周时间线、信息/警告提示、两内容标签页与 49 行（1572 字符）Python 示例。侧栏正文 scrollHeight 约 10,100px；滚动位置从 5630.25→8717.25 时，header top 保持 0，正文无滤镜祖先、无横向溢出。
- 原登录态浅色、深色均实测正式内容标签页。End/ArrowRight 选择后端，Tab 进入关联 tabpanel；Escape 关闭后焦点返回文档卡片。49 行代码的会话剪贴板与页面原文逐字相等。截图 `/private/tmp/fusion-acceptance-document-tabs-light.png`、`/private/tmp/fusion-acceptance-document-tabs-dark.png`。
- 刷新新会话后，正式卡片、完整轨迹、25 个表格行（含表头）、两个标签页及 1572 字符代码仍完整；再次打开、键盘切换成功。刷新后截图 `/private/tmp/fusion-acceptance-document-final-light.jpg`。
- 样本 `/private/tmp/fusion-ui-acceptance-20261002/stream-samples.json`，流式截图 `/private/tmp/fusion-acceptance-document-stream.png`。

## 带内容标签页的真实导出

- 本轮长文实际下载于 16:14，Finder 确认落地后仅复制这两份文件到同一验收目录。
- Markdown 11,568 字符、27,220 bytes，与卡片字数一致；HTML 44,694 bytes，包含两个 `fdoc-tab-panel`，前后端专项内容均导出，24 行数据和表头均保留，没有 script。
- 两文件中的 Python 代码与页面原文均逐字一致，1572 字符、49 行；验证的是实际下载文件，不是重新构建的替代 Blob。
- 文件名 `研发新人四周上手手册（UI验收示例）-v1.md` 和同名 `.html`；结果 `/private/tmp/fusion-ui-acceptance-20261002/long-export-check.json`，HTML SHA256 `bc669a9c58cab0ae9b7f766ce1e89d0fb2897503505d74d05a5a550ecfeb316d`。

## 已有长回答与证据范围

- 原登录态已有「AI Agent可观测性方案」会话 `88e534cd-6a9a-4f06-ae2e-9a5f9d114a95`：1 个用户请求、8 个真实代码块、18,614 字符 main DOM 文本，外层 scrollHeight 4938px、clientHeight 731px。这是已有单轮长回答，不记为大量多轮会话测试。
- 四倍 CPU 模拟下实际向上阅读，scrollTop 4206.75→1119.75；点击「回到底部」恢复 4206.75。没有可折叠的长代码块，不把未执行的代码展开分支计为通过。
- 此阶段累计 132.62 秒：ScriptDuration 0.51 秒、LayoutDuration 0.002 秒、RecalcStyleDuration 0.05 秒、TaskDuration 1.52 秒。样本 `long-chat-performance.json`。性能计数不等同 FPS，低端真机 FPS 尚未测量。
- 未记录到控制台 error。本轮已直接验收长文生成、增量阅读、保存与刷新、已有三版本文档、深浅色内容标签页、复制文本及实际两种导出内容；不会从这些场景推断任意规模的多轮会话表现。

## 收尾

- CPU 模拟恢复倍率 1，Performance 采集关闭；媒体偏好没有残留，viewport 恢复原 2205×1029。
- 原杭州会话 `8fdaebbf-890f-404c-9f25-807f7008cd17`、浅色、mimo-v2.6-pro、自动模式与空输入恢复，文档面板关闭；Finder 临时窗口关闭。
- 新示例会话和两组实际导出文件保留供用户查看。未删除测试数据、改动现有用户文档或同步外部平台。
- 本报告和执行台账在本地补记，没有为证据补录再触发发布。UI 代码没有发现需要修复的问题。
