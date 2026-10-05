# 正文阅读第一版

日期：2026-09-30（Asia/Shanghai）。依据用户“先做第一版看看效果”和本轮持续优化、直接发布授权实施。

## 改动与边界

- 正文恢复明确的标题、段落、列表、引用与链接样式，16px 字号、28px 行高、普通文字最大 832px。表格与代码块仍可使用外层更宽空间。
- 表格采用表头底色、行分隔和柔和边框；表头与单元格复用既有引用编号映射，修复占位符泄露。
- 代码块统一深浅色稳定底材、工具条、圆角和行号基线；从 pre 结构识别代码块，修复无语言标记的 fenced code，行内代码保持独立。正文/推理分别保留 12/10 行折叠，复制读取完整内容，模块级 renderer 身份稳定。
- 共 11 个前端源/测试文件，无新依赖。来源/推理整体视觉重做、用户气泡与消息操作属于后续批次；本轮不改 SSE、Redux、持久化或消息列表滚动控制。

## 本地验证

- 新表格引用回归在旧代码失败（占位符 `⟦42⟧` 无按钮），修复后通过，覆盖稀疏编号定位、缺失编号回退及行内代码保护。
- 新无语言代码块回归在旧代码失败，修复后通过，覆盖正文/推理、换行缩进、12/10 行边界、流式增量与展开状态；另保护折叠时复制完整内容。
- 8 个目标文件共 103 项测试通过；目标 ESLint、本地构建和 diff 检查通过。构建跳过类型检查，不宣称全量类型检查通过。
- 提取原有 Chrome 会话 `c7301b0c-7d06-4b77-951a-32677988a230` 中真实回答的 DOM 标记，结合实际 CSS，在离线测试浏览器检查深浅色前后样式。新正文 h1 为 24px，段落为 16px/28px，宽 832px；列表标记为 decimal，表格保留约 1134px 宽度。此为本地静态证据，不等于原登录态已加载新版本。
- 截图 `/private/tmp/fusion-body-v1-1-0.png`（浅色）、`/private/tmp/fusion-body-v1-1-1.png`（深色），前一版对应 `/private/tmp/fusion-body-v1-0-0.png`、`/private/tmp/fusion-body-v1-0-1.png`。
- 实际 CodeBlock ReactSSR 离线样本覆盖深浅色、12 行折叠与 16 行完整显示；行号与代码的行框偏移为 0px，上内边距 16px、行高 24px。长行可横向滚动到上限，工具条不横移，没有整页横向溢出。截图 `/private/tmp/fusion-code-block-visual.png`。
- 最后补齐三处等宽字体变量的回退：模拟全局 Geist 字体变量未定义时，旧声明实际继承 system-ui，新声明保持 ui-monospace 系统等宽栈。基线和横向滚动测量仍通过；字体修复后的本地构建、diff 检查通过。证据 `/private/tmp/fusion-code-block-font-metrics.json`。
- 390px 窄屏离线样本正文为 15px/26.25px，页面宽度等于视口，无整页横向溢出，表格保留自身横向滚动容器。截图 `/private/tmp/fusion-body-v1-mobile.png`；不等于真实手机页面验收。
- 独立复审提交 `0a0f1d8e`，未发现本轮引入的可达 P0/P1；复核了引用映射、pre/code 识别、实例稳定、复制/折叠与 CSS 宽度继承边界。

## 发布与真实页面

- PR [#214](https://github.com/HyxiaoGe/fusion/pull/214)，源提交 `0a0f1d8e7a0dc144564a2e2d0c9019f25415e44f`、字体修复 `f1dc56248ac57ca4a2fff5efa952ab3e47558707`；初版 CI [36670605860](https://github.com/HyxiaoGe/fusion/actions/runs/36670605860) 成功，最终提交 CI [36671313102](https://github.com/HyxiaoGe/fusion/actions/runs/36671313102) 成功（全量前端 lint、216 个测试文件通过/1 个跳过，2,642 项通过/35 项跳过、Docker 生产构建成功）。PR 合并为 `b291697166035a1ac90c91890ac894065b532b47`，master CI [36671821156](https://github.com/HyxiaoGe/fusion/actions/runs/36671821156)、dev 发布 [36671821604](https://github.com/HyxiaoGe/fusion/actions/runs/36671821604) 成功。
- UI 于 2026-09-30 13:10:53 accepted（Asia/Shanghai）。发布台账 current_sha 与目标合并 SHA 一致，实际 fusion-ui 容器 running，运行 ref/image ID 与台账完全一致：digest `sha256:41ed2923bcef8f7ad32b7cb7cedc711c083a91e2146abd66cb5989b9540e652a`，image ID `sha256:ee2fe530cb609eee2d236fb2ff82ae0edc7e684555af1f1e3e5d8f8f41c7ee56`。健康检查、browser smoke、Record accepted UI release 均成功；API 正常跳过。
- 复用原有 Chrome 扩展标签与登录态，刷新原会话后直接读取并观察新排版：h1 24px/600/33.6px，正文 16px/28px、宽约 832px，段落下间距 16px，列表 decimal；表格 14px/23.1px、宽约 1150.5px。截图 `/private/tmp/fusion-body-v1-live.jpg`。
- 自然键盘 Home 向上阅读后，滚动稳定到 0，出现“回到底部”；点击后回到 993.75px（上限 994px），入口消失。此为已有回答的阅读滚动检查；新生成中的高度增量/展开状态由本地回归覆盖，没有再发模型请求。
- 切换到已有“国产大模型2026对比”会话 `784af61a-97be-4c17-b0f6-d39f7f7b6f31`，六列表格共 5 行，框宽 1152px、单元格内边距 12px/16px、自身横向容器保留；表格内无 `⟦` 占位符。点击“552B MoE”单元格里的引用 42，打开回答依据面板，实际高亮 `answer-evidence-used-search-19`，标题为“DeepSeek V4.1 Flash：更强、更快、更普惠”，位置在可见面板内。验证的是既有引用映射和界面定位，不复核回答本身的模型事实。截图 `/private/tmp/fusion-body-v1-citation-live.jpg`。
- 关闭面板后回到原会话 `c7301b0c-7d06-4b77-951a-32677988a230`，页面停在文章开头供用户查看，空草稿、空知识库选择与自动执行模式保持原状。
- 深色、窄屏和代码块有本地离线展示及回归证据；本轮未改变用户主题设置，未声称原登录态的深色、真实手机或代码块交互已验收。
- 已有报告与用户预览目录保留，源改动 11 文件已发布。本轮发布后的报告/台账补记保存在本地，不另外触发共享部署。
