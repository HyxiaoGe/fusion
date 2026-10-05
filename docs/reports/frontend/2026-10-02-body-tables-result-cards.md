# 2026-10-02 聊天正文表格、代码与天气地点卡片

## 范围与实现

用户要求将天气、地点卡片纳入本轮正文优化，并沿用此前「完成后直接发布 dev」的授权。本轮实施表格、代码交互和两类结果卡片；引用预览与长文目录仍作为后续方向。

- 表格保留原生 table/th/td 与引用编号映射；新增实际溢出检测、方向提示、边缘提示与键盘入口。长表格在 28rem 内层滚动，表头固定，短表格不加滚动提示。组件模块级绑定，增量新增行保持滚动层实例。
- 代码保留高亮、行号、默认折叠与复制完整内容；补齐复制中、成功、失败与重试，内容变化/卸载后的旧回调不覆盖反馈。代码滚动层支持键盘聚焦；控制按钮统一 GlassHoverLens 和 token。
- 天气突出日期/今天/温度，地点将名称、评分、参考消费、地址、营业时间分层。详情和展开控件提供 GlassLens 与键盘反馈；隐藏供应商六位分类编号。保留 HTTPS 图片/链接筛选、图片失败切换与原图预览、默认三条最多五条。
- 阅读正文及其祖先不新增 backdrop-filter。控件支持减少透明度和减少动画；中英文文案使用 i18n。

## 本地证据

- 初次七个目标测试文件 131 项通过；全量 CI 暴露旧代码边界测试未初始化 i18n 后，补齐测试环境并跑八个文件 134 项通过：MarkdownTable、MarkdownRenderer、CodeBlock、markdownCodeComponents、StructuredToolResults、ItineraryResults、ChatMessageList、AssistantResponseStack。分类编号隐藏补充后，相关英文地点用例再次通过；目标 ESLint、diff --check、生产 build 通过。
- 离线真实组件样本通过 light、dark、dark-reduce、light-reduce、incremental 五场景：无页面/卡片横向溢出，无阅读正文滤镜祖先；减少透明度时镜片 backdrop-filter 为 none。
- 实际键盘 ArrowRight/End 后表格横向/纵向位移，th 与滚动层 top 均为 326.3125px，表头保持可见。代码 ArrowRight 可横向滚动，失败后重试复制完整内容；地点 Space/Enter 展开收起。
- 样本：`/private/tmp/fusion-body-cards-fixture/measurements.json`；截图 `/private/tmp/fusion-body-cards-local-light.png`、`/private/tmp/fusion-body-cards-dark-checked.png`、`/private/tmp/fusion-body-table-local.png`、`/private/tmp/fusion-body-code-local.png`。这是本地组件证据，不等同已有登录态验收。
- 独立只读审查未发现新增可达 P0/P1；核对异步清理、稳定渲染器、引用映射、安全 URL 及交通卡样式作用域。

## 发布与真实页面

- 本轮提交 `7fb47cacd069bb9eac43315c2fd0dabe04d4fe0e`，PR [#234](https://github.com/HyxiaoGe/fusion/pull/234)。初次 PR CI `36976828870` 因旧代码边界测试缺语言资源失败（2679 项通过、3 项失败、35 项跳过）；修复保持原有断言，修复提交 `2eb0f687eba205f8760c5dd2c4a89cca97644ee4` 的 PR CI `36977370754` 成功（2682 通过、35 跳过、Docker 构建及 required gate 通过）。PR 于 15:19:01（Asia/Shanghai）合并为 `5276f28919bd314296322b116cb17c0cfd15dddd`；master CI [36977921320](https://github.com/HyxiaoGe/fusion/actions/runs/36977921320)、dev 发布 [36977921705](https://github.com/HyxiaoGe/fusion/actions/runs/36977921705) 均成功。
- UI 于 15:24:28（Asia/Shanghai）accepted，健康和 browser smoke 通过，API 正常跳过。SSH 核对运行容器为 running，台账 SHA 为上述合并提交，digest 为 `sha256:cf80a8b1391d259bac82276b7ba1c4ef01b922164f22be9aae94e7c026612462`，image ID 为 `sha256:e3fe6377b8054a8db3c783872929db901aaa9a26da17dd925b4a56229fddf3d3`，实际身份一致。
- 原有 Chrome extension profile `sean`，已绑定杭州会话 `8fdaebbf-890f-404c-9f25-807f7008cd17`；发布前确认实际天气四天、多个地点区、照片与无图均存在。保留空输入和原会话。
- 发布后复用同一 Chrome 标签与登录态。杭州会话实测四天天气、今天标记与温度层次；地点含照片/无图两类，Space 展开由 3 条变 5 条、Enter 收起回 3 条，焦点保持在原按钮。图片原图预览正常，Escape 关闭后焦点返回图片入口。六位供应商分类编号不再出现；详情链接仍为 HTTPS 且带 noopener noreferrer，未跳转外部网站。
- 浅色、深色稳定画面均已检查；页面与卡片无横向溢出，天气与地点内容无 backdrop-filter 祖先。通过标签 CDP 临时模拟减少透明度/动画：两种主题操作按钮回到实色（background-image none）、装饰 lens display/filter 为 none。已清除媒体偏好并恢复浅色、原视口 2205×1029、杭州会话、空输入和自动模式。
- 已有「国产大模型2026对比」会话 `784af61a-97be-4c17-b0f6-d39f7f7b6f31` 六列表格实测窄视口溢出提示与 tabindex=0；ArrowRight/End 产生横向 24.75px、纵向 246px 位移，th 与滚动层 top 同为 149.84765625px。引用 42 正确打开并高亮「DeepSeek V4.1 Flash：更强、更快、更普惠」，随后关闭侧栏并清除临时视口。
- 已有「AI Agent可观测性方案」会话 `88e534cd-6a9a-4f06-ae2e-9a5f9d114a95` 展示 8 个真实代码块；复制按钮成功显示「已复制」，代码滚动层可聚焦且无滤镜祖先。尝试在空输入中自然粘贴时，工具报告虚拟剪贴板无数据；未发送消息，输入仍为空。因此真实系统剪贴板字节未核验，完整内容复制及失败重试仅有本地浏览器/回归证据。
- 真实截图：`/private/tmp/fusion-body-cards-live-light.png`、`/private/tmp/fusion-body-cards-live-dark.png`、`/private/tmp/fusion-body-table-live.png`、`/private/tmp/fusion-body-code-live.png`。未新增会话或调用模型；真实新回答流式生成、低端机帧率未测，不能由静态或离线样本推断。
