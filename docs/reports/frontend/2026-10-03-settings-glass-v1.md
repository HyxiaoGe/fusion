# 设置界面 Glass 第一版

本轮从 `cfdf9e777814f2285b34daefafef15f048b68f5b` 的 master 开始，按持续优化并直接发布 dev 的已有授权完成两个设置入口。仅提交本任务前端文件；已有台账、报告、个人导出及 `glass-preview-check/` 保留，本文与台账补记留在本地。

## 范围和行为

独立 `/settings` 与用户菜单弹窗共用设置材质：导航、主题、模板和次要操作沿用 GlassLens，编辑和管理正文保持实色，没有模糊祖先。个人设置和数据管理限宽 64rem，管理列表保持较宽空间。卡片框、标题区、按钮与键盘焦点对齐，主题/模板有唯一选中状态。

模板按草稿内容选中，手动修改后取消选中；输入框有明确标签、字数、未保存状态和超限错误。保存用同步 ref 与 disabled 双层守卫避免重复提交和响应覆盖编辑；失败保留草稿。接口、Redux、持久化与管理员显示规则沿用旧代码。中文模板原内容逐项一致，新文案与模板说明走中英文 i18n 和现有 token。

数据备份/恢复分栏，导入覆盖提示有明确警告色。降低透明度时导航和 Glass 控件切为实色，动效按系统降级。主版审查发现无定义 error/warning token，发布前已改为现有 danger/warn，全部新 CSS token 均存在。

## 验证与发布

本地 8 个设置及相关管理测试文件 120 项通过，全量 232 文件 2820 项通过。最终编辑/弹窗 22 项复核；窄窗口宽度修正两个入口 26 项通过；聚焦滚动修正目标 31 项、全量 233 文件 2825 项通过，新增 5 项真实焦点/几何回归覆盖返回首末项、已可见选项、非分类焦点、快速连续切换、选中字重更新和卸载。目标 lint、各次生产构建、diff 检查通过。独立代码审查无阻塞问题。全仓 TypeScript 保留与基线逐条相同的 39 项旧错误，未标记全仓类型检查通过。

| 版本 | PR / 提交 | CI / dev |
| --- | --- | --- |
| 主版 | [#246](https://github.com/HyxiaoGe/fusion/pull/246)，`fe0c3cd4d03ac24e4b42363cf9d74dedcdae42aa` → `510024701b6ac1c1e3823b798ca1a80ab0156053` | PR [37029741516](https://github.com/HyxiaoGe/fusion/actions/runs/37029741516)、master [37030633544](https://github.com/HyxiaoGe/fusion/actions/runs/37030633544)、dev [37030634770](https://github.com/HyxiaoGe/fusion/actions/runs/37030634770) 成功 |
| 导航修正 | [#247](https://github.com/HyxiaoGe/fusion/pull/247)，`624a54fefd1594f7ea8251c990f1822c193a6c0d` → `658a3553ec1e6209ff0d20d67b3cdee93a4df574` | PR [37031334818](https://github.com/HyxiaoGe/fusion/actions/runs/37031334818) 成功，master [37032437133](https://github.com/HyxiaoGe/fusion/actions/runs/37032437133) 与 dev [37032437979](https://github.com/HyxiaoGe/fusion/actions/runs/37032437979) 成功 |
| 聚焦滚动修正 | [#248](https://github.com/HyxiaoGe/fusion/pull/248)，`21e1ce7159ffaca7d3525523d4177922c8d0586a` → `dd55a0301265f4348a7f3da8da71548347bab030` | PR [37034768853](https://github.com/HyxiaoGe/fusion/actions/runs/37034768853) 成功，master [37035589473](https://github.com/HyxiaoGe/fusion/actions/runs/37035589473) 与 dev [37035589897](https://github.com/HyxiaoGe/fusion/actions/runs/37035589897) 成功 |

主版 2026-10-03 00:03:40（Asia/Shanghai）accepted，实际 SHA/digest/image ID 与发布台账一致，容器 running；候选健康与发布 browser smoke 成功，API 正常跳过。Windows 2785 项通过、35 项跳过。

主版真实 801px CSS 窗口中，旧运行时分类内容 94px、按钮 90px；新版内边距会进一步挤压。英文 Runtime configuration 的内容 170.2px，也挤占 170px 按钮的内边距。跟进修正保留文字自然最小宽度，宽屏分配剩余空间，窄窗口由原容器横向滚动；左对齐避免首项落到负方向。移除固定列数和 52rem 断点，不改权限、选择与焦点协议。独立代码/依赖阅读确认 Radix 键盘 focus 默认允许滚动；但实际导航返回首项仍未移动到可视范围，不能以依赖默认行为代替真实验收。

## 原 Chrome 登录态实测

复用同一 Chrome 标签、同一用户及既有清单会话 `abc3cc76-edc0-4a76-97d8-70b22a935f9d`。没有本地服务、替代浏览器或新标签。

- 浅色/深色主题唯一选中、键盘 Enter 操作及稳定后文字/背景通过。减少透明度与动效模拟下导航/模板/主题 filter 均 none，lens display none，编辑祖先无 backdrop-filter；临时模拟清除。
- 键盘工程师模板 68 字、唯一选中与未保存提示正确；手工编辑 17 字后选中取消。1000 字可保存、1001 字禁用且出现 invalid 和红色警告；还原恢复已保存的 0 字。英文 Writing assistant 模板实际选中与内容正确，Reset 清空，Escape 等到弹窗隐藏。未保存临时偏好。
- 原 2205×1029 CSS 视口中，弹窗约 1280×875px，编辑区约 968.5px；正文 698px/滚动内容974px。聚焦输入滚动276px，顶部导航位置174.67px稳定。独立页同宽，无页面横向溢出，个人卡片与操作区清晰。
- 数据分栏、覆盖提示及两个入口的键盘分类切换通过。知识库列表2个、服务用量加载、运行时3张标题卡、模型已注册18/候选137正常；管理区域约1230px，保留列表宽度。MCP入口正常，未改管理配置。
- 尺寸/主题/media 切换存在过渡；瞬时几何和颜色不作为稳定验收结果。此前一次瞬时读数被误称固定撑高，已向用户纠正并重新测量稳定布局。

导航修正 2026-10-03 00:18:37（Asia/Shanghai）accepted，运行版本与发布台账一致。801px CSS 视口中运行时按钮从 90px 扩至约 118px，容纳 94px 内容及左右内边距，所有分类自然最小宽度正确。原标签自然 End 操作使末项完全可见；Home 将焦点和选择移至首项，但 scrollLeft 仍为 94.5px、首项 left 为 -22.95px，被裁切。随后实现设置导航局部 onFocusCapture：绘制帧中按最终选中宽度，只调整该导航水平位置；忽略焦点已移走或已卸载回调。独立审查通过，不扩展共享 Tabs 协议。

最终修正 2026-10-03 00:46:50（Asia/Shanghai）accepted。SHA `dd55a0301265f4348a7f3da8da71548347bab030`、digest `sha256:527e7f45e35818a974e32f5799998a924c3eca9194f8ecdc08419e90befd4d55`、image ID `sha256:27a4352b481093e9ea6814cfc6e7d4fb24ef422b855350d7f48f202890b2cb36` 与实际容器逐项一致，容器 running。PR/master CI 与 dev 均成功，候选健康及发布 browser smoke 通过，API 正常跳过；Windows 2790 项通过、35 项跳过。

最终原标签刷新后的真实检查：

- 801px CSS 弹窗中文 End 末项完全可见，scrollLeft 94.5px；Home 后 scrollLeft 6px，首项回到可见边缘、焦点及选择正确，页面 scrollY 仍 0。浏览器 clientLeft 为整数 1px，CSS 边框为 0.75px，几何验收按实际边框及 1px 的缩放取整容差判断，未将 0.25px 取整误差视为内容裁切。
- 同一弹窗英文 End scrollLeft 446.25px、Home 归零，首末项均完整可见。Runtime configuration 按钮约 197.9px，容纳最长标题与两侧 12px 内边距，选中状态不挤压文字。
- 同一标签独立页英文 End scrollLeft 417.75px，Home 归零，首末项完整可见，页面 scrollY 均为 0。七分类恢复后再操作，未将 SSR 初始三分类当作权限结果。
- 原会话、中文、浅色、2205×1029 CSS 视口恢复，媒体模拟已清除，回答偏好仍 0 字且保存禁用，聊天空草稿、自动模型和推理开启保持。临时英文/窄窗口无遗留。

最终截图：[浅色设置弹窗](/private/tmp/fusion-settings-glass-light.png)。验收在同一已登录标签进行，没有新会话、模型调用或偏好保存。

## 缺口与边界

保存成功/失败、保存中锁定和重复点击由回归覆盖；真实页面未改写用户回答偏好。导入导出布局和覆盖提示实测，未实际导入覆盖用户数据或生成备份。普通用户三分类由现有单元测试覆盖，真实登录为管理员七分类；权限消失时独立页回退不足属于原代码可达推断，本轮不扩展权限逻辑。触摸设备未实测，管理员内部复杂配置控件尚未逐项美化。
