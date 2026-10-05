# 设置弹窗焦点与表单控件

用户授权同时完成两项：关闭弹窗返回本次入口，统一设置输入、下拉、复选与错误反馈。沿用直接发布 dev 的授权。基线为 master `ad21cea3ffb2827fdcdbd197c32430f1491f2ec3`，本轮仅提交 19 份前端文件；已有台账、报告、导出及本地预览保留。

## 实施与回归

根因：受控 Dialog 没有 DialogTrigger，Radix 默认关闭逻辑无法返回实际按钮。新增设置专用焦点 hook，显式捕获触发按钮，保留默认初始焦点，取消/Esc/成功关闭后恢复有效入口，入口隐藏/禁用/移除时回所属管理区。mounted/open/Content 身份与父弹窗状态守卫阻止卸载、快速重开、退出动画旧回调抢焦点，已经安置的外部焦点不覆盖。

头像/UserMenu 与外层设置通过 ClientLayout 的局部 provider 连接，不把 DOM 放入 Redux 或用全页首个头像猜入口。知识库菜单转弹窗仅抑制该次菜单恢复，普通 Esc 保留菜单默认处理。MCP、模型操作、知识库新建/编辑/确认/分块预览均捕获本次入口。共享 ConfirmDialog 与分块预览仅新增可选回调，不改其他调用默认行为。

沿现有 SettingsControls 新增输入、多行、Select、原生受控 checkbox、错误说明包装器。字段实色背景，蓝色焦点和下拉选中、灰色禁用、红色校验，错误关联 aria-describedby；长选项换行，工具选择行有背景/边缘标记。下拉 portal 自带实色材质，不建立模糊层。减少动效/透明度保留降级。MCP 鉴权换成既有 Radix Select，四种枚举、changeForm、保存、凭证引用与白名单协议保留。模型/知识库的 API、权限、CAS、确认原因、重入和持久化逻辑未修改。

新增 32 项测试，含原故障对照、失效入口、菜单转弹窗、嵌套关闭、卸载/快速重开及两个真实头像入口；实际管理组件取消/Esc 不调用修改 API，MCP 鉴权下拉第一下 Esc 只关选项，第二下退出编辑器。原存储与参数断言保留。

236 文件 2861 项全量测试通过，全仓 ESLint、生产构建和 diff 检查通过。顺序 tsc 最终与基线逐条一致的 39 项旧诊断，本轮无新增；测试新建登录态曾漏 system_prompt，已补齐并重跑该测试与类型比对。首次 MCP Select 集成在 jsdom 缺 scrollIntoView，添加仅测试 shim 后实际 Radix 流程通过。独立只读审查未发现新增可达 P0/P1，主代理核对实际 diff。

## 发布前真实基线

复用原 Chrome 扩展、原用户标签与登录态，会话 `3fce3479-4ae6-4e1f-8c10-84be4aab950f`。当前中文、浅色、2205×1029 CSS，设置选中 MCP 服务。MCP 三服务健康启用，授权数 2/11/1。新增编辑器打开后取消，直接观察 activeElement=BODY，确认原故障。截图 `/private/tmp/fusion-settings-forms-focus-before.png`。

## 发布与线上验收

实现提交 `ab75551d37a81846635b5040d4428cb361195da8`，[PR #250](https://github.com/HyxiaoGe/fusion/pull/250) 的 CI [37045797114](https://github.com/HyxiaoGe/fusion/actions/runs/37045797114) 全部通过，Windows 镜像测试 2826 通过、35 跳过，共 2861。02:20:27（Asia/Shanghai）合并为 `04b3706e93e016dda68f3635afea4846cda51093`，触发 master CI [37046768019](https://github.com/HyxiaoGe/fusion/actions/runs/37046768019) 与 dev [37046768630](https://github.com/HyxiaoGe/fusion/actions/runs/37046768630)。master CI 与 dev 均 success，API 正常跳过。02:26:10（Asia/Shanghai）accepted。只读 SSH 核对运行 SHA 与合并提交一致，容器 running，运行 ref/image ID 与接受记录一致。digest `sha256:e3d1bef35dc34f28f698779fd428acdc4b2f98702afbd9b2be3a8feec325f8a2`，image ID `sha256:9c4b41f9ca6b86e15b4a843b47dbc0bd5a4f560be922a2cc0fdbf394061dfa6d`。候选健康与发布 browser smoke 成功。完整日志下载曾遇到网络 EOF，缩小到部署 job 后成功；只读 GitHub 检查曾自动审批超时，按工具允许重试一次后成功，没有残留审批阻塞。


原 Chrome 标签刷新后直接实测：外层设置默认初始分类聚焦，Esc 返回本次 sean 头像；从头像 Enter 开菜单，再打开设置正常。MCP 从刷新 Tab 到新增、Enter 开编辑器，名称 Tab 到提供商；鉴权下拉 ArrowDown 展开，第一下 Esc 仅关闭选项并返回选择框，第二下关闭编辑器回新增按钮。新增取消和 Context7 编辑 Esc 均返回对应按钮。空表单保存只触发客户端校验，名称/提供商/endpoint 错误出现，aria-describedby 关联；键盘聚焦错误字段时边框与轮廓保持红色。传输方式禁用灰底/次文字，opacity=1；Context7 两个已授权 checkbox 选中蓝色与行底/边缘可见，Tab 到下一项可用，没有改变授权。

知识库新建名称初始焦点正确，名称空时保存禁用；描述多行编辑后 Tab 到业务类型，取消返回新建按钮。菜单→编辑在菜单迟到关闭阶段后仍保持名称焦点，Esc 返回本次菜单按钮；删除确认初始取消按钮焦点，取消后返回同一按钮，普通菜单 Esc 仍正确。原长文档 23 分块预览 1/3→2/3（第11块）→1/3 可用，Esc 返回第一份文档的预览按钮，未误返回第二份。

模型仍为 18 已注册/18 可选择、137 候选。搜索 DeepSeek 显示 2/18，清空按钮 absolute 且边界在输入内，清空恢复；提供商下拉保留图标，键盘选择 DeepSeek 显示 2/18 与1/137，随后恢复全部提供商。隐藏 DeepSeek V4 Flash 确认初始原因焦点正确，空原因确认禁用，Tab 从原因→取消→跳过禁用确认到关闭按钮→循环回原因，Esc 返回该模型按钮。没有提交隐藏或上线操作。

MCP 浅深色错误/禁用/选中与键盘高亮均直接观察并截图。浅色聚焦轮廓实际 `oklch(0.55 0.18 260)`，错误 `oklch(0.577 0.245 27)`；深色错误 `oklch(0.7 0.2 27)`，编辑背景 `oklch(0.18 0 0)`。菜单实色、backdrop-filter=none，选中勾与键盘高亮可区分。801×900 CSS 窗口下，外层设置 clientWidth/scrollWidth=719，编辑器=510，菜单=143，均无横向溢出且位于窗口内。模拟减少透明度/动效后字段和菜单保持实色无模糊，transition/animation 为全局降级的 0.01ms。

最终恢复中文、浅色、2205×1029、原会话、原 MCP 分类，临时媒体偏好均 false，编辑器关闭，输入草稿0、执行自动、模型自动、推理开启；三个服务仍启用健康、授权数2/11/1。页面 error 日志为空。Context7 预设只用于未保存的展示草稿，已取消。没有创建/编辑真实配置、修改授权、删除/重建/上传知识库、写入个人偏好或调用模型；没有新建浏览器、标签或本地服务。真实页面登录为管理员，普通账号、触摸设备与实际保存/失败/忙状态没有实测，相关协议仍由既有回归覆盖。真实配置写入不在本次验收范围，未声称通过。

截图 `/private/tmp/fusion-settings-forms-focus-light-after.png`、`-light-error.png`、`-dark-error.png`、`-dark-select.png`、`-knowledge-light.png`、`-narrow-reduced.png`；实际配色及最终状态 JSON 保存在同名前缀。报告和台账补记留本地，任务源码均已合入。
