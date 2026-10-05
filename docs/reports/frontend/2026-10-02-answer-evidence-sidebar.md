# 回答依据侧栏与来源选中反馈

日期：2026-10-02（Asia/Shanghai）。本轮按用户补充的“包括选中效果”实施，沿用此前直接发布 dev 的授权。

## 实施结果

- 头部、关闭按钮、关键词展开入口与外链按钮接入现有 GlassHoverLens；来源列表保持稳定实色，装饰滤镜仅位于交互按钮的独立子层，阅读内容没有 backdrop-filter 祖先。
- 来源卡片显示实际引用编号，选中后呈现实心编号、信息色边框和左侧条、浅色底与“当前查看”标记。选中标题完整展示，未提供引用编号的旧来源不伪造连续编号。
- 点击卡片或使用键盘选择来源，外链按钮仍独立打开原网址。选中只是临时查看状态，不修改来源的已使用/候选分组、事实或统计；新的正文引用定位优先，关闭、来源身份替换后不会沿用过期选择。
- 搜索关键词使用原生 details 默认折叠，保留全部关键词及计数，来源列表更靠前。调整文案走中英文 i18n，支持减少透明度和动效。
- 只提交五个前端任务文件，既有报告、执行台账、预览及旅行文件保留。

## 本地验证

- 六个目标测试文件共 56 项通过，覆盖稀疏稳定编号、键盘选择、分组不变、新定位覆盖、关闭重开、来源身份替换、原生展开、减少动效、定时器清理和英文标签。
- 全量前端 228 个文件、2750 项测试通过；全量 lint、生产构建和 git diff --check 通过。
- 全仓 TypeScript 仍是已存在的 39 个错误，与前轮基线的错误集合逐项比较没有新增，不能标记为类型检查通过。
- 独立只读复审没有发现新增可达 P0/P1；核对来源选择隔离、知识库按需加载、原生键盘行为与定时器清理。
- 日志保存在 /private/tmp/fusion-evidence-sidebar-targets.log、fusion-evidence-sidebar-full-tests.log、fusion-evidence-sidebar-lint.log、fusion-evidence-sidebar-build.log 和 fusion-evidence-sidebar-types.log。

## 发布记录

- 提交：`4d465b5e4a8bf53c71029fe73c7a6a789d4c90ab`。
- PR：[#238](https://github.com/HyxiaoGe/fusion/pull/238)。
- PR CI：[36999458822](https://github.com/HyxiaoGe/fusion/actions/runs/36999458822)，成功，required gate 通过，API 正常跳过。
- 19:18:24 合并为 `b3a1769376a5c5364a4d6050e12bb13e2e1e3931`，本地 master 已快进，既有文件保留。
- master CI：[37000280385](https://github.com/HyxiaoGe/fusion/actions/runs/37000280385)，成功，required gate 通过。
- dev 发布：[37000281115](https://github.com/HyxiaoGe/fusion/actions/runs/37000281115)，成功。候选 smoke、browser smoke、accepted 记录成功，API 正常跳过。
- 19:23:37 dev 台账记录目标 SHA。实际容器 running，image ID `sha256:b85ef20eafabd114d818f5d4e0e5ec5c45ff46a1bba82ee3b5910fb13104ca5a`、digest `sha256:82e2f94c444f49d895758940c4954a438d062c3e1f6e9d36defc1efb80000752` 与台账一致。
- 已在原 Chrome 登录态完成下方真实页面验收。

## 页面基线

复用官方 Chrome 扩展、原 sean 配置与用户现有标签。当前为国产模型对比会话 `784af61a-97be-4c17-b0f6-d39f7f7b6f31`，浅色、2205×1029 CSS 视口；回答依据侧栏打开，引用 55 对应东方财富 Qwen3.8-Flash 来源。未新建会话或调用模型。

基线截图：[优化前](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-before.png)。

## 真实页面验收

发布后刷新同一标签、同一会话加载新版本，没有新建会话或调用模型。

- 正文表格点击实际引用 55，侧栏正确定位东方财富 Qwen3.8-Flash 来源，呈现实心编号、完整标题、信息色边框与“当前查看”。同时只有一个 aria-current 和一个 aria-pressed=true，侧栏打开时正文引用预览数量为零。
- 当前来源外链为 `https://wap.eastmoney.com/a/202608273855533933.html`，独立于选择按钮，保持 target=_blank。未点击外链或新开网页。
- 从关闭按钮按 Tab 聚焦原生关键词 summary，引用 55 仍选中；空格展开全部九条关键词，再次空格收起。Tab 到来源 10 时焦点描边可见，但仍未改变选择；空格后来源 10 成为唯一当前项，页面 URL 不变。
- 实际点击来源 42，正确显示已深读 DeepSeek V4.1 Flash；对候选来源 1 按 Enter 后，当前项仍位于“候选来源”。已使用 41 条、候选 47 条、深读 2 个网页以及两个分组的 41/47 个来源均保持不变。
- 对来源按钮按 Escape 关闭，焦点返回原正文引用 55；再次点击该引用，唯一当前项恢复 55，候选来源的临时选择不残留。
- 实际悬停来源卡片，鼠标移动使 glint 坐标从 50/50px 变化为 240/75px，角部折射值跟随变化；稳定后 lens opacity=1，选中状态保持。
- 深色中实心编号使用 `oklch(0.7 0.16 260)` 底与 `oklch(0.18 0 0)` 字，标题清晰，选中边框与当前标记可见。浅深色截图均已查看。
- 1000×900 CSS 视口，页面 scrollWidth=1000，侧栏宽约 440px，没有内部横向溢出。选中标题及其全部祖先没有 filter 或 backdrop-filter。
- 减少透明度/动效模拟下，头部与当前卡片 backgroundImage=none，当前卡片切实色；装饰 lens 全部 display=none、backdropFilter=none，过渡为全局降级的 0.00001 秒。减少动画的自动滚动调用由目标回归核验。
- 媒体模拟和视口覆盖已清除。恢复原 2205×1029 CSS 尺寸、浅色、同一会话、空输入与自动执行模式；侧栏保持引用 55 选中，供用户查看。

截图：[浅色全景](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-selection-light.png)、[浅色选中局部](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-selection-detail.png)、[深色全景](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-selection-dark.png)。

## 证据边界

实际知识库来源按需加载、真实来源替换、新回答流式过程与触摸设备未在本轮实测，相应已有回归证据。真实页面使用已有网页来源；外链仅核对地址与交互结构，没有执行外部跳转。文档版本差异查看仍未实现，不属于本轮。发布后报告与执行台账补记在本地保留，未纳入本轮五文件提交。

## 同日修复：保留网站 favicon

用户指出新版网站图标消失。代码确认引用编号与 UsedSourceIcon 二选一，导致所有有编号的来源不再显示 favicon；原 Chrome 页面直接观察到侧栏 img 数量为零。已在域名旁恢复有编号来源的原 favicon，左侧保留实际编号和原选中反馈，无编号旧来源仍在原位置显示一次图标。

- 修复提交 `6214561a223d0232a9935d37431a0d71c4e74ed5`，PR [#239](https://github.com/HyxiaoGe/fusion/pull/239)，只涉及来源组件和测试两个文件、10 行新增。
- 在既有稀疏引用编号测试中追加选中/未选中卡片都显示且仅显示一个原始 favicon 的断言。修复前该断言失败，修复后六个目标文件共 56 项通过，目标 lint、生产构建和 diff 检查通过。
- 当前用户页面为同一模型对比会话、浅色、2205×1113 CSS 视口，侧栏当前来源 42；后续验收保留这一最新状态。没有新会话、模型调用或本地服务。
- PR CI [37001765803](https://github.com/HyxiaoGe/fusion/actions/runs/37001765803) 成功，required gate 通过。19:43:24 合并为 `99a8a46d0e42785405e25784d8e0b05a1e98b4ea`，本地 master 已快进。master CI [37002574304](https://github.com/HyxiaoGe/fusion/actions/runs/37002574304) 和 UI dev [37002574747](https://github.com/HyxiaoGe/fusion/actions/runs/37002574747) 均成功，API 正常跳过。
- dev 台账于 19:48:22 accepted，容器 running；SHA 与本次合并一致，image ID `sha256:27d253bfa0338ef19207d9a0b1a705009257c08d1536a8ccbf12fb9569332f2f`、digest `sha256:6b3ef89641623f5b5b3de26c434ac6932e09d8f6a7bbc808cb83a07af6662a89` 与台账一致，候选与 browser smoke 成功。
- 原 Chrome 登录态刷新后，从正文引用 42 打开侧栏，实际显示 88 个 favicon 图片节点。当前来源图标 naturalWidth=32、display=block、展示尺寸约 16×16 CSS px，同时保留实心编号、域名、深读标签和当前查看标记。切换来源 55 后其图标仍成功加载，来源统计 41/47/2 保持不变。
- 浅深色中的来源 42 图标实际加载并可见，来源卡片无内部横向溢出。恢复同一会话、浅色、验收前的 2205×1029 CSS 视口、空输入与自动模式，侧栏保持来源 42；本轮没有视口或媒体模拟，也没有新增模型调用。
- 观察到 64 个图片加载成功、0 个等待、24 个原始 favicon 资源加载失败，失败图片沿用旧隐藏行为。例如 qwen.ai、alibabacloud.com、qbitai.com 和部分政府/报纸站点的直接 favicon.ico 地址。本轮修复编号与图标二选一的渲染回归，没有逐一诊断第三方图片失败或改变 favicon 服务策略，不能宣称所有网站图标均可用。
- 截图：[浅色](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-favicon-light.png)、[深色](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-sidebar-favicon-dark.png)。报告与台账补记在本地保留，既有预览和无关文件保留。

## 同日修复：来源整卡玻璃高光

用户提供深色来源 56 截图，指出选中高光在外链按钮前截断。原 Chrome 登录态同一模型对比会话直接复现：整卡宽 407.25 CSS px，左侧按钮中的 lens 宽 361.51px；lens 右边缘与外链按钮左边缘均为 2146.27px，而整卡右边缘为 2189.00px。截图：[修复前](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-frame-before.png)。

- 将现有 GlassHoverLens 和鼠标光线坐标从左侧选择按钮移到共享来源行，覆盖内容与外链区域。装饰层不接受点击，选择按钮与外链仍独立；知识库展开正文位于该行之外。
- 键盘聚焦来源选择按钮时，信息色描边覆盖整卡；外链仍保留自身聚焦反馈。未更改来源状态、地址、图标、分组或统计，没有新增依赖或文案。
- 六个目标测试文件共 56 项、目标 lint、生产构建与 diff 检查通过。视觉回归以实际页面测量与截图核对，没有新增复刻 CSS 实现的测试。
- 提交 `2ff4955b2f68f7bb40f91eb6de4773a195a6e147`，PR [#240](https://github.com/HyxiaoGe/fusion/pull/240)，仅提交来源组件及其 CSS 两个文件。
- PR CI [37004833007](https://github.com/HyxiaoGe/fusion/actions/runs/37004833007) 成功，required gate 通过。20:15:22（Asia/Shanghai）合并为 `d7e354f73720d1865020f636e894195ab1ece2d4`，本地 master 已快进，既有报告、预览和无关文件保留。
- UI dev [37005608107](https://github.com/HyxiaoGe/fusion/actions/runs/37005608107) 成功，候选 smoke、browser smoke 与 accepted 步骤通过，API 正常跳过。20:20:20 accepted，实际容器 running；SHA 与本次合并一致，image ID `sha256:a4c879d849c46c036e85cdd3809382242c060876e8f28a74f34782b0c0ddf027`、digest `sha256:0371003f1ffb6f1c0b9dc7fc8bb635218ba73c471c0357423d73a148b569a80c` 与台账一致。
- master CI [37005607437](https://github.com/HyxiaoGe/fusion/actions/runs/37005607437) 成功，required gate 通过。
- 原 Chrome 登录态刷新同一会话，从正文“国产算力深度绑定”一项的引用 56 打开侧栏。修复后 lens 宽 403.50px，右边缘 2188.25px；外链右边缘 2178.26px，整卡右边缘 2189.00px。高光延伸到外链按钮之外，只保留整卡本身 0.75px 边框；选择按钮内 lens 数为零，共享装饰 pointer-events=none。
- 鼠标从内容区域移动到外链按钮，共享行 glint 从 80/65px 变化为 377.51/26.00px，整行 lens opacity 稳定为 1；外链自身 glint 为 16/16px。实测截图中右侧收边与整卡边缘一致，不再在外链按钮前截断。
- 对来源 56 按空格，焦点位于原选择按钮，完整卡片显示信息色实线描边，选择按钮局部 outline=none；按 Tab 到外链，原选择保持，外链自身描边和共享整行高光均可见。对来源 55 按 Enter、对 56 按空格，唯一当前项分别为 55/56，页面 URL 和已使用 41/候选 47/深读 2 的统计不变；未执行外部页面跳转。
- 浅深色均直接查看实际截图，高光覆盖区域一致。减少透明度/动效模拟下，当前卡片 backgroundImage=none，共享 lens display=none、filter/backdrop-filter=none，选中状态保留。标题及全部祖先未出现 filter 或 backdrop-filter，卡片 clientWidth/scrollWidth 均为 404，无内部横向溢出。
- 临时媒体模拟已清除，本轮未设置视口覆盖。恢复验收前深色、同一会话、2205×1029 CSS 原尺寸、空输入与自动模式，侧栏保持来源 56。网站图标实际加载（48px 原图），本轮没有新会话、模型调用、下载或本地服务。
- 截图：[深色局部](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-frame-dark-detail.png)、[深色页面上下文](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-frame-dark-context.png)、[浅色局部](/Users/sean/.codex/visualizations/2026/09/29/01a0ef3a-7a6d-7853-b303-ff78cd093cbd/evidence-frame-light-detail.png)。真实知识库展开与触摸设备未在本轮实测；报告与执行台账补记本地保留。
