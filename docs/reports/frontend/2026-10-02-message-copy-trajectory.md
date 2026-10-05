# 轨迹入口布局与用户消息复制

## 目标与实现

- “查看轨迹”紧跟轨迹状态标签，取消整行靠右布局；标签与入口归为同组，空间不足时自然换行。默认使用轻量文字按钮，悬停与键盘焦点仍有反馈。
- 用户消息在原有编辑、重新发送操作旁加入复制。复制消息文本原文，保留换行与 Markdown 字符；成功显示“已复制”，2 秒后恢复；失败显示重试提示。仅附件且没有文本时不显示无效复制入口。
- 复用共享操作组件与 `useMessageCopy`，新增文案走中英文 i18n。编辑改变消息文本或组件卸载后，旧复制异步结果不更新新消息反馈、不弹出旧错误。

## 本地验证（2026-10-02，Asia/Shanghai）

- 7 个目标测试文件：92 项通过。
- 全量前端测试：224 个文件、2,723 项通过。
- 目标 ESLint、生产构建与 `git diff --check` 通过。构建配置跳过全量类型验证，未将其记为 TypeScript 检查通过。
- 验证覆盖原文保留、失败重试、仅附件空文本、复制焦点、编辑后旧复制完成、卸载后旧复制失败，以及助手消息既有复制和轨迹回调。
- 本地构建包含已有未提交 `glass-preview-check/`；该目录未纳入提交，CI 构建以提交内容为准。

## 发布与真实页面

- 分支：`codex/message-copy-trajectory-layout`。
- 功能提交：`6ab159e4743961dfefb995928cbc93418b18d884`。
- PR：https://github.com/HyxiaoGe/fusion/pull/235。
- PR CI [36985226789](https://github.com/HyxiaoGe/fusion/actions/runs/36985226789) 与 master CI [36985939740](https://github.com/HyxiaoGe/fusion/actions/runs/36985939740) 成功，Fusion required gate 通过。
- 合并 SHA：`58ca08834476096db9f70a2aee56cc65d5f9d4bf`。UI dev [36985940325](https://github.com/HyxiaoGe/fusion/actions/runs/36985940325) 成功，2026-10-02 16:52:30（Asia/Shanghai）accepted；健康和 browser smoke 通过，API 正常跳过。
- 只读核验 dev 台账与实际容器均为上述 SHA 对应发布：digest `sha256:f45f81cefae1ec0280ac9b7ba792af7c1b2681633b7957832e41ce10f5cb16b5`，image ID `sha256:2f4f7134c5991a940e2e48b9364723865bebc547f998d0324b518ddec6ac2872`，容器 running。

## 原登录态真实页面验收

复用官方 Chrome 扩展、sean 配置与原标签，使用已有会话 https://fusion.seanfield.org/chat/3fce3479-4ae6-4e1f-8c10-84be4aab950f 。没有创建新会话、发送消息或调用模型。

- 用户消息出现复制、编辑、重新发送三项操作。通过编辑按钮 Shift+Tab 进入复制，Enter 激活后显示“已复制”；随后恢复“复制”。操作区在键盘焦点内 opacity 为 1，按钮有可见焦点轮廓。
- 浏览器会话虚拟剪贴板仍返回上一轮 1572 字符文档代码，不能作为本次复制的证据；使用同一 Chrome 原生系统粘贴到空输入框，实得 **286 字符，与用户消息原文逐字一致**。临时输入随后清空，没有发送。
- 键盘打开编辑时原文 286 字符正确，Escape 取消后原文保持。复制不改变消息、不触发重新发送。
- 轨迹入口与状态标签间距 **6px**，`margin-left: 0px`，不再被推到状态行最右端。Enter 打开轨迹，选中该会话第 1 次执行并成功加载真实详情：2 步、1 次工具、轨迹完整；“在聊天中查看”返回对应回答。
- 原显示尺寸 2205×1029 下，文档侧栏加载成功，入口仍在左侧运行状态旁；侧栏边界 x=1325，按钮 x≈615，未被侧栏正文覆盖。文档面板原有模态遮罩拦截背景点击，不能将其记为背景按钮可直接操作。
- 浅色与深色均直接检查了新入口和复制焦点；页面测得的 1000×900 CSS 视口下深浅色无横向溢出。保留 Chrome 原 67% 缩放，临时视口覆盖已清除。
- 最终恢复原会话、聊天视图、浅色、2205×1029、空输入与自动模式，文档面板关闭。复制按钮保持键盘焦点，方便查看新操作。

截图：
- 最终真实页面：`/private/tmp/fusion-message-copy-trajectory-final.jpg`。
- 深色：`/private/tmp/fusion-message-copy-dark.jpg`。
- 文档侧栏：`/private/tmp/fusion-trajectory-document-light.jpg`。
- 1000×900 深浅色：`/private/tmp/fusion-message-copy-narrow-dark.jpg`、`/private/tmp/fusion-message-copy-narrow-light.jpg`。

真实页面覆盖已有文本消息的成功复制和键盘交互；拒绝复制、空文本附件和迟到异步结果由目标回归测试覆盖。本轮未发现需要追加修复的 UI 问题。已有报告、预览页及无关文件保留；本报告与台账验收补记在本地保留。
