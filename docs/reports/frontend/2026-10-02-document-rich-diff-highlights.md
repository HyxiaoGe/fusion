# 文档富内容差异高亮尾项

日期：2026-10-02，Asia/Shanghai。

## 范围与结果

补齐只读版本对比中统计卡标签、数值，以及提示块、时间线、统计块、标签页标题的具体变化。沿用已有增绿、删红、改蓝，不修改文档内容、版本数据、普通阅读和导出结构；无后端或新依赖。

解析器仅在需要对比高亮时通过 WeakMap 记录 LF 归一化后的 UTF-16 源位置，去强调符号和 trim 同步保留映射。嵌套统计内容与跨忽略容器的正文合并以 -1 标记虚拟换行，避免重复文本、非连续源位置被猜错。普通阅读和流式预览不生成字符位置数组。

## 本地与独立审查

- 基线：`696c03751448a2227a30db3151e0dd226fe9fc1d`。
- 任务提交：`3d44e67509dc5f5c681858f16ada9f7613b9b906`；PR：[245](https://github.com/HyxiaoGe/fusion/pull/245)。
- 目标 6 个测试文件 65 项通过；全量 231 文件 2,813 项通过。
- 构建、改动文件 ESLint、diff 空白检查通过。
- `tsc --noEmit` 保留基线既有 39 项错误，错误首行逐项一致，无新增；不能写成全量类型检查通过。
- 独立审查未发现可达 P0/P1；独立运行解析器、Markdown、对比视图 3 文件 36 项通过。
- React 复核：解析与投影共用依赖一致的 memo；字段组件不增加状态、请求或 effect，使用安全 React 文本节点，保留标签页焦点、ARIA 和阅读路径。
- 回归覆盖重复字段、CRLF、UTF-16、强调符号、嵌套和未闭合容器、默认标题、普通与展开标签页、键盘选择、静态导出无差异标记。

日志保存在 `/private/tmp/fusion-rich-diff-{target,full,build,lint,types}.log`。未启动本地服务；`glass-preview-check/` 与已有报告、台账及其他用户文件未纳入任务提交。

## 发布与真实页面

PR [245](https://github.com/HyxiaoGe/fusion/pull/245) 合并为 `cfdf9e777814f2285b34daefafef15f048b68f5b`。PR CI [37025132751](https://github.com/HyxiaoGe/fusion/actions/runs/37025132751)、master CI [37026094095](https://github.com/HyxiaoGe/fusion/actions/runs/37026094095) 和 dev [37026095526](https://github.com/HyxiaoGe/fusion/actions/runs/37026095526) 均成功。dev Windows 测试 2778 项通过、35 项跳过；候选健康与发布 browser smoke 通过，API 正常跳过。

23:24:10（Asia/Shanghai）accepted。只读 SSH 核对运行 SHA 与上述合并一致，容器 running，发布台账 digest/image ID 与实际运行一致：

- digest：`sha256:9fc77fac42677571f79e3cb87e8982d71fe8c562ae7a3ce69e1fd93e089d9412`。
- image ID：`sha256:a0480377643a4c07f143ed454e2254a94ee11035d647a2deec8516694eac6266`。
- 运行证据：`/private/tmp/fusion-rich-diff-runtime.json`；发布日志：`/private/tmp/fusion-rich-diff-dev-steps.log`。

发布前直接读取既有「杭州周末游攻略」v1 → v2：5 处变化中没有统计字段变更，两份「行程思路一句话」标题未变化；用于后续确认未变标题不会误标。不创建新版本或调用模型强制凑验收数据。

复用官方 Chrome 扩展原标签、sean 登录态，发布后刷新实际页面验收：

- 既有「新员工首周清单」v1 → v3：发布前两份「提问的正确姿势」均无 mark；发布后一份 `modified` 蓝色，一份 `added` 绿色。总计新增 12、修改 146、删除 4 个 mark，六个改变的表格单元格保留。
- 导航到 10/11，实际浅色/深色截图均看到新增提示块标题及正文高亮，层次与文字可读。正文到根的祖先未发现 filter/backdrop-filter。
- 普通 v3 阅读在刷新、关闭重开和主题往返后均恢复 4191.75px；4 张统计卡正常，正文无版本差异 mark。
- v2 → v3 仍只有午餐变化，渲染条目及原文分别标绿；切回 v1 的 11 个变化和 10/11 导航正常。
- 发布后再次打开既有旅行文档，导航 1/5，两份未变「行程思路一句话」标题仍无 mark，没有把正文改动误投影到标题。
- 最终恢复原清单会话、浅色、原尺寸、空草稿与自动模式；清单 v3 对比 v1 留在变化 10/11 供查看。本轮没有创建会话、调用模型、改写文档或保存设置。

截图：`/private/tmp/fusion-document-rich-diff-light.png`、`/private/tmp/fusion-document-rich-diff-dark.png`。统计数值/标签及时间线、统计块、标签页标题的真实版本变化没有现成样本，由回归测试覆盖；不能将其写成线上实测。触摸设备、新长文流式及新的导出文件内容未做本轮真实验收；普通/静态导出不带差异标记有测试证据。

## 后续设置美化范围

代码阅读：设置导航外框目前使用旧的半透明背景与硬编码深色 slate，内部控件已有共享样式，页面下的个性化、数据管理及其他模块主要使用普通 Card。

同一原 Chrome 标签只读查看 `/settings`：实际有 7 个导航项；在 2205px 主区宽度下，个性化文本框宽 2107.5px，表单铺满屏幕。未点模板、保存、导入或管理操作；未改变设置，返回原清单会话。截图 `/private/tmp/fusion-settings-before.png`。

下一轮可先约束编辑内容宽度和布局层级，再统一导航、卡片外框和操作区材质，优化个性化模板、输入区与保存反馈；长表格和编辑正文保持稳定实色。该方向尚未实施；此处仅是现状读取，不能写成设置优化已验收。
