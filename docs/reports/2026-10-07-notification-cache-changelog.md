# 通知筛选缓存与更新日志业务

记录时间：2026-10-07（Asia/Shanghai）。本轮在 `codex/notification-cache-changelog` 实施，基线为 `8d6073b28ebe9b5f1efb681fc56f8973def9f218`。用户授权两项实施，并明确更新日志先提供发布接口，暂不做编辑页面。本轮终点为本地实现与验证，尚未推送、发布或向真实用户投递日志。

## 用户行为

- “全部／未读”各自缓存已加载列表、展开页数和分页游标。15 秒内切换立即恢复内容并不重复请求；过期时保留内容后台刷新。面板打开检查新鲜度，失败后的“重试”强制请求。
- 可见页面继续每 15 秒同步，恢复焦点、在线和可见状态时核对；已读、业务变更和来源删除事件使缓存失效。账号切换清空两份缓存并取消在途请求，旧列表不能回退全局未读数或覆盖新账号。
- 五种生成结果归入 `ai_conversation`（AI 对话生成）；更新日志归入 `changelog`。通知记录显示业务名称，统一使用现有全部／未读、铃铛未读角标和全部已读水位。
- 更新日志通知进入 `/updates/{id}`，头像菜单中的“更新日志”进入 `/updates` 历史列表。列表、点击通知和读取详情接口均不会直接已读；成功加载的正文进入视口且页面可见才提交当前用户的回执。手动从历史或地址进入详情同样适用。
- 回执失败保留未读并提供重试；账号、日志切换及卸载会取消旧请求。原 AI 对话的精确消息／运行跳转及重复定位 nonce 保留，移动侧栏继续在目标导航执行后的冒泡阶段关闭。

缓存位于当前账号的通知 Provider 内存中；含用户状态的 API 保持 `private, no-store`。本轮沿现有通知模块扩展，没有新增缓存依赖、广播队列或插件框架。

## 发布与读取接口

| 接口 | 权限 | 行为 |
| --- | --- | --- |
| `POST /api/admin/changelogs` | 既有管理员鉴权 | 创建不可变更新日志并向事务中取得的已有本地用户投递通知 |
| `GET /api/changelogs?limit=20&cursor=...` | 登录用户 | 返回摘要页与 `next_cursor`，不传正文、不修改已读 |
| `GET /api/changelogs/{id}` | 登录用户 | 返回正文和当前用户的 `notification_id`，无关联通知则为 `null` |
| `POST /api/notifications/read` | 登录用户 | 复用既有单条／批量通知已读接口，正文呈现后调用 |

发布请求示例（仅文档样例，未执行）：

```json
{
  "version": "2026.10.07",
  "title": "通知中心更新",
  "summary": "统一查看 AI 对话生成结果与产品更新。",
  "content": "## 新功能\n\n- 通知筛选缓存\n- 产品更新通知\n"
}
```

版本 1–64 字符，首字符为字母或数字，其余允许字母、数字、`.`、`_`、`+`、`-`；标题最多 120 字符、摘要 500 字符、Markdown 原文 100000 字符，均不能全为空白。版本、标题和摘要规范化首尾空白，正文保留缩进与末尾换行。

所有响应沿既有 `ApiResponse` 包装。发布正文和通知在一个数据库事务内完成；同版本、相同保存内容重试返回原记录，不重复投递；同版本不同内容返回 409。当前不提供编辑、草稿或删除接口。新注册用户可查看历史，不补发旧版本的未读通知。发布 API 会写入通知，应在 API/UI 升级完成后由管理员明确发布真实内容。

## 持久化与兼容

迁移 `b8e2f4a6d0c1` 接续 `6a1e9f3c8b20`，增加不可变更新日志表、通知 `business_type`、`changelog_id`、用户／日志唯一约束与来源形状 CHECK。旧通知默认归入 AI 对话，已读时间、创建水位和账号 revision 保留；AI 唯一约束、对话删除级联和精确结果归属校验继续生效。

统一未读数涵盖两种业务；`unread_conversation_ids` 只投影 AI 对话，避免日志通知出现空对话标识。发布唯一版本仅允许首次插入者广播，用户按固定升序申请通知状态锁。事务失败时正文、通知和修订水位一起回滚。存在已发布日志时迁移拒绝降级，避免静默丢失正文。

## 验证证据

- 前端 9 文件 62 项最终回归通过：筛选缓存、展开分页、TTL、全部已读同步、旧请求／账号隔离、通知目标分派、用户菜单入口、日志历史／详情、正文可见已读及 Markdown 安全渲染。
- 缓存新回归在旧 Provider 上有 5 个失败，修复后通过。审查发现两新页首帧语言差异；真实 `renderToString(中文) → hydrateRoot(英文)` 两项在修复前失败，固定中性首帧后通过且没有恢复错误。
- 后端 9 文件影响面验证 212 项、18 个子测试通过，覆盖通知、终态投影、存储、对话和继续生成、会话缓存及轨迹收敛；最后正文保真修正后的日志目标 34 项通过（属于前述影响面的最终子集复验，新增 3 项，不将重复项相加）。包含事务回滚、用户独立阅读、来源 CHECK／FK、幂等、SQLite 两个独立 Session 并发发布同／不同版本、历史兼容和普通用户／匿名权限检查。
- 受影响前端 ESLint、后端 13 文件 Ruff／格式及最后两文件复验、`git diff --check` 通过。最终 `next build` 成功，产物包含 `/updates` 与 `/updates/[changelogId]`。
- 完整 TypeScript 检查仍有 39 条既有诊断，与从当前 HEAD 建立的相同依赖基线逐字一致，没有新增；不记作全仓类型检查通过。构建与读取生成类型顺序执行后的最终结果用于比较。
- 独立代码审查覆盖缓存、鉴权、事务／锁顺序、来源迁移、目标分派、正文显示及回执归属，未发现新增可达 P0/P1。首帧与正文保真后续修正另行复核。

前端最终命令从 `frontend/` 执行：

```sh
node node_modules/vitest/vitest.mjs run src/components/notifications/NotificationCenter.test.tsx src/components/notifications/NotificationsProvider.test.tsx src/components/changelogs/ChangelogPages.test.tsx src/components/changelogs/ChangelogContent.test.tsx src/components/changelogs/useReadPresentedChangelog.test.tsx src/components/layouts/UserAvatarMenu.test.tsx src/components/layouts/MainLayout.test.tsx src/lib/api/notifications.test.ts src/lib/api/changelogs.test.ts
node node_modules/next/dist/bin/next build
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

后端影响面命令从 `backend/` 使用已有 Python 环境执行：

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider test/test_changelogs.py test/test_notifications.py test/test_terminal_failure_projection.py test/test_repositories.py test/test_conversation_service.py test/test_chat_service.py test/test_chat_continue.py test/services/agent/test_session_cache.py test/services/agent/test_trajectory_reconciliation.py -q --tb=short
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider test/test_changelogs.py -q --tb=short
```

SQLite 升级／无内容降级／有内容降级保护实测通过，PostgreSQL INSERT 与升级 DDL 编译验证通过。本轮未运行真实 PostgreSQL 并发、CI、部署或已登录页面验收；这些层不记作通过。未启动 Fusion 服务、发布真实日志或创建模型测试消息。原工作树与独立工作树中此前已有的文档改动保留。
