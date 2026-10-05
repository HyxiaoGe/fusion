# 文档版本只读差异查看

日期：2026-10-02，Asia/Shanghai。用户限定范围为只读对比、标题与正文差异、变化导航，沿用直接发布 dev 的授权。

- 复用现有完整版本快照读取接口，不改后端、数据库或依赖。侧栏默认比较上一版，允许选择更早的版本；首版入口禁用，单版本不展示入口。
- 单栏展示修改前/后，标题独立变化，正文按完整 Markdown 块比较。中文短文字按字素突出增删，表格、代码和自定义容器完整渲染，实际原文差异按需展开；未变内容折叠但仍可展开。
- 差异计算有预算，超限合并中段但保留共同前后缀，两侧原文可精确重建。6 万字符最坏计算目标测试耗时约 10ms，属于本机测试，不代表低端设备帧率保证。
- 比较请求按认证身份、文档及两个版本归属；旧请求取消，错误响应不能参与比较，失败可重试。比较不写阅读位置，返回正文恢复原位置，导出仍沿原当前版本路径。
- 7 个目标测试文件、51 项通过，目标 lint、生产构建及 diff 检查通过。全仓 TypeScript 为 39 项既有错误，本次任务文件无诊断，不标记全仓类型通过。
- 独立复审发现文末短变化无法靠近顶部时计数回退，已改为保留显式导航目标直到手动滚动，并识别手动滚动到底。对应回归与独立复核通过，未发现新的确认可达 P0/P1。
- 首次提交仅纳入 9 个前端任务文件，后续最小修复均限于对应组件；既有报告、执行台账、预览及旅行文件保留。发布身份与真实页面结果另列下方。
- 已提交 `31e82eeca5f01c2be91b9c0f7fb33073dc4e76fb`，PR [#241](https://github.com/HyxiaoGe/fusion/pull/241)。TypeScript 当前错误集合与前一轮基线逐项相同。
- PR CI [37007863910](https://github.com/HyxiaoGe/fusion/actions/runs/37007863910) 成功，required gate 通过，API 正常跳过。20:45:15（Asia/Shanghai）合并为 `aad062d54855f97c38e57244454a4866a5cd9836`，本地 master 已快进。
- 首次 dev [37008602594](https://github.com/HyxiaoGe/fusion/actions/runs/37008602594) 在 Windows runner 的 Docker 全量测试失败，镜像推送和 dev 部署均跳过：新加文末导航回归期望 scrollTop=300，实际为 0，其余 2742 项通过、35 项跳过（并行项目统计）。保留原断言，将加载后的计数/滚动重置由普通 effect 改为 layout effect，避免内容出现后才执行的重置覆盖已经发起的导航；正在重新验证与交付，不记为已发布。
- 最小修复 `7af4b4f3d7716018e819c8a5942d848ae7e9dad4`、PR [#242](https://github.com/HyxiaoGe/fusion/pull/242)，只修改组件 import 和 reset hook。修复后 51 项目标测试、lint、230 文件 / 2778 项全量前端测试、生产构建通过；独立复核与 8 项组件测试通过。Windows 全量重跑是线上修复的必要证据，尚未以本地测试代替。
- 修复 PR CI [37009620596](https://github.com/HyxiaoGe/fusion/actions/runs/37009620596) 与 required gate 成功，21:04:05（Asia/Shanghai）合并为 `4768497dd31e2bb5988cf998aad3609d26b1f538`，待本次 master/dev 及真实页面结果。
- 本次 dev [37010558660](https://github.com/HyxiaoGe/fusion/actions/runs/37010558660) 成功。Windows 全量实际统计为 2743 项通过 / 35 项跳过，新的差异组件 8 项、算法 17 项和面板 6 项均通过；保留原文末导航回归断言。21:09:26 accepted，候选和 browser smoke 通过，API 正常跳过。
- 运行核对：SHA `4768497dd31e2bb5988cf998aad3609d26b1f538`；digest `sha256:822652d78621c81a331e8975a33531a2408f5507206b14f3467b39132f7a83c1`；image ID `sha256:50042524b6f198c5ec4ed8b69acbc6a06c5a7b573917959c0518be29c60cf9f1`，UI 容器 running，实际 image ID/ref 与发布台账一致。

## 真实页面基线

复用官方 Chrome 扩展、原 sean 登录配置和同一标签。当前用户原页面为模型对比会话、浅色、2205×1029 CSS 视口，输入为空。本轮通过现有侧栏进入“新员工首周清单”会话 `abc3cc76-edc0-4a76-97d8-70b22a935f9d`，没有创建会话或调用模型。

已有“新员工入职第一周清单（按天执行版）”包含 v1/v2/v3（5046/5119/5137 字）。原版本菜单实际切换核对：v1 无“参加部门新人培训”和“和入职引导人约一次午餐”，v2 有培训但无午餐，v3 二者都有；第三版原阅读 scrollTop 为 4191.75px。发布前没有差异入口；随后恢复 v3 以便核验新版行为。

## 首轮已发布版本真实验收

- 同一 Chrome 标签刷新，v3 正文恢复到 4191.75px。进入差异默认 v2 → v3，仅一个列表块修改，修改前无午餐、修改后有午餐；两段未变内容默认折叠，原文差异可展开。复杂列表原文按整块着色，没有宣称仅新增一行着色。
- 改选 v1 → v3，11 个实际变化块包含总览表 Day 3、培训标题/目标/任务和 Day 5 午餐。表格两侧保持完整，键盘展开未变内容后保留 Day 1–Day 5 章节和总览表。
- Enter 导航到变化 2，焦点进入变化 2，比较区 scrollTop=832.5px，window/main 均为 0；连续到变化 11，scrollTop=4032px（scrollHeight=4879、clientHeight=848，文末截短），计数稳定 11/11，下一处禁用；上一处返回 10/11。手动滚动到底也跟踪为 11/11。
- 返回正文恢复 4191.75px；当前版本切到 v2 后比较归属为 v1 → v2，没有串入 v3 午餐。v1 只读正文、差异禁用；关闭重开 v3 为普通阅读并保持原位置。标题修改、纯增删、错版本/旧请求和账号变化由目标回归验证，当前真实样本标题一致，未宣称在线标题修订路径实测。
- 深色、1000×900 CSS 视口、减少动效/透明度下导航实际通过；控件在视口内，无内部横向溢出，对比正文祖先无滤镜，view 为实色。临时模拟和视口覆盖已清除，浅色、2205×1029、空草稿恢复，现有清单 v3 对比 v1 保留用于查看，没有新会话、模型调用或文档改写。
- 本次 master CI [37010558174](https://github.com/HyxiaoGe/fusion/actions/runs/37010558174) 与 required gate 成功。
- 最后浅色实测识别 12px 修改后绿色标签在白底对比度约 3.39:1。追加两条 CSS 修正：语义红/绿 70% 与现有正文色 OKLab 混合，浅色修改前/后计算为 8.23/6.26:1，深色为 9.26/11.58:1；生产构建、diff 检查通过，不改计算、接口或状态。修正提交 `98e2f4409e2be550e2ef101b26e2e702ab9e3681`、PR [#243](https://github.com/HyxiaoGe/fusion/pull/243)，待后续发布和实际颜色复验。
- 最终颜色修正 PR CI [37012207269](https://github.com/HyxiaoGe/fusion/actions/runs/37012207269) 和 required gate 成功，21:28:41（Asia/Shanghai）合并为 `bcdf003e7adadf41649aa0be37bbc9e2dff29563`，本地 master 已快进。等待最终 dev/master 和浏览器标签颜色复验。

## 最终交付结果

- 最终 master CI [37013250113](https://github.com/HyxiaoGe/fusion/actions/runs/37013250113) 和 dev [37013250781](https://github.com/HyxiaoGe/fusion/actions/runs/37013250781) 成功，Windows 全量再次为 2743 项通过 / 35 项跳过，差异组件 8 项通过；21:33:49（Asia/Shanghai）accepted，候选健康与 browser smoke 通过，API 正常跳过。
- 最终运行 SHA `bcdf003e7adadf41649aa0be37bbc9e2dff29563`，digest `sha256:aa7a92be6a062886805fb7299ea7a11d6517c56c612ad6bd6f88b0273e50aeee`，image ID `sha256:16773959a782e477761633a9134c7ba47f3e0d464e84068288e8dfaa640fb1e1`；UI running，运行 ID/ref 与发布台账一致。
- 原 Chrome 标签刷新后，实际浅色标签为 `oklab(0.4474 0.152808 0.0778594)` / `oklab(0.4775 -0.0988901 0.0525808)`，白底计算为 8.23/6.26:1；深色实际标签为 `oklab(0.7855 0.124741 0.0635587)` / `oklab(0.8135 -0.0988901 0.0525808)`，深色背景计算为 9.26/11.58:1，均高于 4.5:1。
- 最终恢复原浅色、2205×1029、空草稿，媒体模拟与视口覆盖清除。v3 普通阅读仍为 4191.75px；页面保留已有清单 v3 对比 v1、变化 2/11、焦点在变化 2，供用户查看。首次失败和后续修复均保留以上记录，未改变文档内容、创建会话或调用模型。
- [最终浅色截图](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/document-version-diff-light.png)、[最终深色截图](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/document-version-diff-dark.png)、[首轮窄屏及降级截图](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/document-version-diff-narrow.png)。
