# Issue #127：查证来源证据与工具自述回归（2026-09-23）

时间按 Asia/Shanghai。先阅读[首轮](2026-09-23-postmerge-functional-regression.md)、[第二轮](2026-09-23-second-round-tool-claim-regression.md)报告及[动态发现切换条件](../2026-09-23-issue71-next-phase.md)。本轮复用用户已登录的 Chrome 标签，向同一 dev 环境的 DeepSeek V4 Flash 发送同题、交错的发现路径与旧路径请求；每个 Run 均从数据库读取完整 `agent_events`，核对 `tool_call_started`、`tool_call_completed` 与同一 `tool_call_id` 的来源状态。页面、数据库、容器直连、代码阅读各自作为独立证据。真实页面运行的是部署中的旧代码，不能当成本分支改动已发布的验收。

## 代码定位与最小改动

- 代码：`agent_loop_request_prep.py` 中发现实验的 `requires_catalog_evidence` 固定为 `False`；本轮没有更改。它不能承担逐页原文来源的证明。
- 代码：现有 `verified_web_v1` 与包无关，要求实际 `url_read` 返回非空正文，并以读到的来源约束最终引用；无正文或引用不匹配时，交付安全退路。原 PostgreSQL 请求中的“官方文档”未命中 `requires_verified_source_evidence`，所以此前发现 Run 走 `standard`。本分支只将“官方文档”纳入现有明确来源信号，不改默认入口、阈值、`product_answer_validator.py` 或 `verified-research` Skill。
- 本地测试：原 PostgreSQL 原句在修改前断言为红（`standard`），修改后为 `verified_web_v1`；无读页却自称“两次抓取”的候选回答被拒绝。两页脚本覆盖一页正文为空时仅引用成功页、错误引用失败页被拒、两页均有正文时两条引用通过。已有测试还覆盖所有读页为空。脚本执行器不发真实生命周期事件，`started` 的真实性仅由下面的数据库样本证明。

## 实页与完整事件流

发现路径在匹配原句的一次 `/api/chat/send` 上显式加入 `dynamic_tool_discovery=true`，发送后立即恢复 `fetch`；旧路径不传此开关。数据库 `run_config` 复核各次实际路径。下面序号均指同一 Run 的完整事件流，所列 `url_read` 均有对应 `started` 与完成事件；“轨迹完整”只表示序号连续，不表示任务完成。

三组同题原句分别为：

1. 全失败：“请核验两份指定页面的原文是否出现短语 GOLD-127-A 与 BLUE-127-B。请逐一打开 https://fixture-127-a.invalid/a 和 https://fixture-127-b.invalid/b，按实际取得的正文判断；读取失败就说明未核验。”
2. 部分成功：“请核验 PostgreSQL 18 官方文档 COMMIT 页对事务修改的作用，以及第二个指定页面是否包含短语 BLUE-127-B。请逐一打开 https://www.postgresql.org/docs/18/sql-commit.html 和 https://fixture-127-b.invalid/b。每项结论分别附上实际读到的原文来源；某页读不到就明确说未核验。”
3. 全成功尝试：“请核验两页原文：逐一打开 https://example.com/ 和 https://httpbin.org/html，分别告诉我网页标题并各附对应来源；若有一页读取失败就明确说明。”后续补测改为 `https://example.com/` 和 `https://www.example.com/`，其余要求保持一致。该类实验两臂均指定 `plan_mode=off`，避免自动计划本身的形状失败混淆读页结果。

构造前的探索样本曾使用“分别打开”，代码中的禁读正则把其中的“别打开”误作禁止联网，使发现路径不授权 `url_read`；这属于**路由错**，因此正式配对统一改用“逐一打开”，不把探索样本混入读页成败统计。另一个探索句的“不用搜索替代”也触发禁网信号。本轮未修此独立路由问题。

| 场景与路径 | 页面观察 | 数据库事件与归类 |
| --- | --- | --- |
| [全失败·发现](https://fusion.seanfield.org/chat/f093b5ae-b9b2-48f6-bcbf-b0c186764284)，Run `f9720465be374509921aaceaaf6e0217` | 对两条 `.invalid` 页未下原文结论，显示部分完成与无可靠结论退路；没有虚构成功抓取。 | 102 条连续事件 `0–101`；#76、#77 两次 `url_read started`，#81、#85 均 `degraded/read_degraded`。`verified_web_v1`；**工具真失败**，退路成立。 |
| [全失败·旧](https://fusion.seanfield.org/chat/afd0a6fa-08e7-42fc-950d-ade50d1c66fc)，Run `a6cf97f266d342869912adf5305d7557` | 明说“逐一尝试打开…两次读取都失败了，页面正文没有取得”，未断言目标短语。 | 88 条 `0–87`；#61、#62 `url_read started`，#66、#70 均 `degraded/read_degraded`；另有 #27 `web_search` 成功。自述与账本相符；**工具真失败**。 |
| [部分成功·发现](https://fusion.seanfield.org/chat/3f29a562-4a4d-4470-8450-0268defa5b15)，Run `aedef089fbe64ea29fd3988b236132b6` | 只对 PostgreSQL COMMIT 页给原文级结论和来源；对 `.invalid` 页明确未核验。 | 69 条 `0–68`；#41 COMMIT、#42 失败页 `started`；#50 COMMIT `success/read_success`，#46 失败页 `degraded/read_degraded`。部署版仍是 `standard`，本次正确回答不能证明守卫生效；失败页属**工具真失败**。 |
| [部分成功·旧](https://fusion.seanfield.org/chat/b5a19993-f960-4167-998f-15991b7da910)，Run `1f4696b60b6a434b863c8199e1517cd0` | 同样只对 COMMIT 给原文结论，第二页标为未核验。 | 38 条 `0–37`；#12、#13 `started`，#21 COMMIT `success/read_success`，#17 第二页 `degraded/read_degraded`。自述相符；**工具真失败**。 |
| [全成功尝试·发现](https://fusion.seanfield.org/chat/0e23959f-45d0-4409-8859-95dcdbf73498)，Run `ff7870fae833412896956c11a139d526` | 对 `example.com` 与 `httpbin.org/html` 两页给出读取结果，并区分缓存快照与标题字段限制。 | 74 条 `0–73`；#30、#31 两个目标 `url_read started`，#35、#39 均 `success/read_success`；另 #53 `www.example.com` 成功。`verified_web_v1`；两个目标确实读到正文。 |
| [同题旧路径](https://fusion.seanfield.org/chat/85734a34-1bc9-4831-aeed-6796692f385b)，Run `4aa8ba63a5f8421e9eaa0215ff1f366d` | `example.com` 成功，`httpbin.org/html` 明说失败。 | 37 条 `0–36`；#11、#12 `started`，#16 一页成功、#20 另一页 `degraded`。**工具真失败**；因此该配对不能证明“全成功时两路径均放行”。 |

全成功场景又以 `example.com` / `www.example.com` 同题交错运行：[发现](https://fusion.seanfield.org/chat/779fa9e6-4557-4bee-8330-94b974ed9bb3) Run `cfdfac8ab0f043b3ba38af31e475b545` 为 51 条连续事件，#25/#26 开始、#30 成功、#34 降级，页面只给成功页结论；[旧路径](https://fusion.seanfield.org/chat/5f392db4-45d3-4e6b-814f-b48d76a2723e) Run `946fb58629b04958996ea23adb119c91` 为 38 条连续事件，#11/#12 开始、#16/#20 均成功，页面给两页结论，但页面只显示 1 条已使用依据，两页都标成 `[1]`，模型也承认编号冲突。这是**引用对应关系不清**，不能仅据两次读页成功就宣称每页来源映射合格。再次[发现路径重跑](https://fusion.seanfield.org/chat/93c740af-af10-4553-9a79-e69422d85d03) Run `009fb37b393d4f63b63d622897f47f88` 为 125 条连续事件，最初两次目标读页降级，后续替代地址虽有成功读取，Run 最终 `error`、页面显示“生成失败”及安全退路。**尚未取得同题两臂目标页全部成功且来源对应清楚的一对样本**；不能把单臂成功拼成配对通过，也不能据此计算成功率。

### 额外的工具事实反例

[旧路径原文回答](https://fusion.seanfield.org/chat/321deae4-bf4a-4259-abad-ae2121424a6e)，Run `511591142f1a483a9ad113a9f819a346`，页面写“两个页面均读取成功，内容已逐条核对”，并称“我已按你提供的 URL 直接读取”。数据库 31 条事件 `0–30` 中只有 #11 **一次** `url_read started`，#14 完成为成功，#16 来源为 `.../sql-rollback.html`；没有 COMMIT 页的 `url_read started`。这是**证据不足／工具活动编造**，不能把它记为 COMMIT 工具失败。此样本走旧路径，说明本分支的发现策略不会修复默认旧入口。另一个旧路径样本曾把 ROLLBACK 目标“尝试一次”说成一次，事件实际有两次 started；它同样属于工具次数自述不符。

## #107 同轮对照

原句均为“你是谁？再查一下明天北京天气”。[发现路径](https://fusion.seanfield.org/chat/85d680c3-aa2f-4b48-b08a-945710398607) Run `fd852968e8b3478089abd16906f29244`：49 条事件 `0–48`，#18 `tool_search`、#32 `weather_forecast` 均成功；页面有北京天气卡片及天气文字，**漏答身份**。[旧路径](https://fusion.seanfield.org/chat/f248098c-82f2-44c4-a9d1-07d5c2f01444) Run `1f797eef4d5f4b61bec73c935fbb0c08`：14 条事件 `0–13`，无工具 `started`；页面答身份，却声称不能取实时天气，并错误写“北京 7 月”。前者是**复合任务答案缺项**，后者是**路由／执行缺失并伴随无依据月份**；两条都未满足原请求。#107 保持开放，不能记为发现路径实测收益。

## 验证范围与结论

- **页面**：全失败与部分成功配对的回答没有对未读正文给原文结论；发现全成功单臂可交付，两路径全成功配对样本不足；旧路径另有 COMMIT 工具事实编造。页面未观察到本分支发布效果。
- **数据库**：上述每个 Run 查询的是完整 `agent_events`，而非只看完成侧；序号连续与工具结果分别记录。来源事件和开始事件通过 `tool_call_id` 关联，搜索候选不等于读页正文。
- **代码**：仅扩充已有明确来源信号；`verified_web_v1` 的实际正文及引用约束由现有工作集承担；`requires_catalog_evidence` 仍为 `False`。本地 2 个新增目标测试通过，另 3 个子测试通过，Ruff 检查和格式检查通过。本地全量相关测试的两处失败在未改动基线也复现：受沙箱限制的本地监听，以及 SQLite 对 PostgreSQL `JSONB` 的类型编译；尚需远端容器 CI 判定。
- **容器直连**：目标公共页可达性曾在 dev 容器预检，但不能代替 Fusion 的 `url_read`。本轮没有依据容器日志推断调用或正文。

本分支可供审查；#127 的**代码侧反例验证通过，发布后页面验收和同题全成功配对尚缺**。保持动态发现默认关闭，不据此关闭 #127 或 #107。更多定向重跑可能只是在抽样工具提供方波动；下一步优先由 PR CI 验证代码，发布后用已固定的失败／部分／成功样本回归，再决定切换条件是否满足。
