# 设置剩余子页美化收口

日期：2026-10-03（Asia/Shanghai）。授权：完成原计划第 3 项，发布 dev 后结束设置美化。

## 范围与行为

- 服务用量和运行时配置沿用设置共享按钮、语义状态标签，补齐统计块、摘要、详细记录及加载/失败/空态层次。
- 同一只读监控状态组件用于 Firecrawl、Resend、运行时配置，加载及失败保留所属服务，加载为 status、失败为 alert。
- 运行时刷新期间保留已读配置和同一按钮节点；按钮禁用、转动图标及 aria-busy 表达刷新中。失败显示原快照并明确提示，再次刷新可恢复。
- 成功空快照分别说明没有生效配置及没有版本记录；未知用量继续显示占位，不转成零。API、权限、用量缓存、统一刷新注册表、日期格式化与统计口径保持。
- 统计/长列表使用实色，不添加 backdrop-filter。标题和边缘沿现有 SettingsSurface，减少透明度/动效沿模块降级。新增文字写入中英文 i18n。
- 两个设置入口复用相同子页（代码阅读确认）；本轮不新增配置修改、导出、探测或其他功能。

## 本地验证及审查

- 四个受影响测试文件：33 项通过，其中新增 4 项覆盖初始错误重试、成功空态、刷新保留/更新及刷新失败恢复。
- 既有邮件测试等待完成数据卡，避免将新加载标题误当作已加载状态。
- 目标 ESLint、Next 构建、diff --check 通过。
- TypeScript 39 项既有错误，与上一轮 `/private/tmp/fusion-settings-forms-focus-types-final.log` 逐条一致，无新增；未称全仓类型通过。
- 独立代码审查：无当前改动引入 P0/P1。无真实路径结论由代码审查代替。

## 发布与真实页面验收

- PR：[#251](https://github.com/HyxiaoGe/fusion/pull/251)，实现提交 `ed3d080695352c60adc13d6995fdb7425d1f961e`。
- PR CI：`37076189511` success，235 文件 2830 测试通过、1 文件 35 项平台测试跳过；发布契约另跑 35 项通过。生产镜像构建成功。
- 合并提交：`560c314841596d9283cc5a9081bc3856f9b153d4`；master CI `37076820567`、dev `37076821050` 均 success，API 正常跳过。dev Windows runner 的 Docker 测试为 2830 通过/35 跳过，生产构建、候选健康与发布 browser smoke 均成功。
- 改版前原 Chrome 标签连接成功：原会话 `3fce3479-4ae6-4e1f-8c10-84be4aab950f`，中文浅色，2205×1029，空草稿。
- 改版前服务用量真实余额、请求、月/日额度与同步时间正常读取。运行时 3 生效配置、19 版本记录、0 需关注，无编辑入口。
- 07:23:29（Asia/Shanghai）记录 accepted 发布。只读 SSH 核对运行 SHA 为上述合并提交，容器 running，镜像 digest/ref/image ID 与发布台账一致：
  - digest `sha256:441e85a140b051c2f5458a7fbbbeffcb0cddb3054fd75e7f3817b987967c105c`
  - image ID `sha256:7d2417308e5f7bddb667ac4160e8f563a59d97f3324b78c84287b36cbe3d2f56`
- 原登录态真实路径实测：初始用量/配置加载显示所属服务；统一刷新禁用、加载、成功恢复正常；详细记录通过 Tab 聚焦 summary，有实线焦点，并可 Enter 展开每日及历史记录。
- 原生 summary 内的 span 不可键盘聚焦，初次工具定位其 span 超时；读取 DOM 后改为 summary 操作通过，属工具定位问题，未改变产品代码。
- 运行时真实刷新显示“刷新中”、aria-busy=true，保留原列表和刷新按钮焦点；成功后 aria-busy=false、按钮启用。统计仍为 3/19/0，版本记录的生效状态和分类标签语义配色正常。
- 浅深色实测，正常尺寸与 801×900 窗口无横向溢出，窄窗口服务用量转单列。设置正文/统计没有带 backdrop-filter 的祖先。
- 模拟减少透明度与减少动效，实际媒体匹配 true；两个子页标题背景图为 none，标题和按钮光层 blur 为 none。结束时清除模拟、重置 viewport。
- 最终核对：原 URL 与会话、中文浅色、2205×1029、设置关闭、空草稿、自动模式/自动模型、推理开启恢复，焦点回到原头像。最初批量关闭后立即切主题被退场状态吞掉，读取实际状态后单独恢复浅色并验证成功。验收期间 console error=0。
- 未对真实配置、授权、模型或知识库做写入，没有模型调用。空配置、查询失败和旧快照失败恢复由回归覆盖，本轮线上未主动制造这些状态。普通账号、触摸与独立 /settings 路由未实测；两入口共用相同子页来自代码阅读。
- 本轮第 3 项完成，设置美化收口，不追加新优化范围。

### 页面证据

- [服务用量浅色](/private/tmp/fusion-settings-closeout-usage-light.png)、[深色](/private/tmp/fusion-settings-closeout-usage-dark.png)、[窄窗口降级](/private/tmp/fusion-settings-closeout-usage-narrow-reduced.png)
- [运行时配置浅色](/private/tmp/fusion-settings-closeout-runtime-light.png)、[深色](/private/tmp/fusion-settings-closeout-runtime-dark.png)、[窄窗口](/private/tmp/fusion-settings-closeout-runtime-narrow.png)、[版本记录](/private/tmp/fusion-settings-closeout-runtime-versions.png)


## 文件边界

仅十个任务前端文件纳入 PR。既有报告/台账、用户导出文件和本地 `glass-preview-check/` 保留，本报告及台账补记留本地。

完成核对时间：2026-10-03 07:29:58。
