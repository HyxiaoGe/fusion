# 身份字面层收窄：历史原句与实测边界

**首轮同组输入收窄前后的最终包结果一致、无回归；不代表历史原句全部正确或模型后续输出恒定。** 6 条复合请求在收窄前即失败，本次未改变，由 [#107](https://github.com/HyxiaoGe/fusion/issues/107) 独立跟踪其业务缺陷。

基线 `4678e3f0`，临时进程仅将 `_is_identity_request` 换成核心 `fullmatch`；部署源码未改。模型 `deepseek-chat`，原硬预算/重试策略保持。每行来自真实 resolve 调用；缓存命中状态未取得，不能当独立上游准确率验证。离线结果、真实模型结果与mock测试分开。

### 首轮临时候选与基线的逐条对照

| 出处 | 原句 | 期望包 | 收窄前：层 / 包 | 候选收窄后：层 / 包 | 结果 |
|---|---|---|---|---|---|
| 76402a7d | 你是谁？ | direct | literal / direct | literal / direct | 通过 |
| 35ed52da | 你是谁？再查一下明天北京天气 | weather | failed / clarification_only | failed / clarification_only | 基线已失败，候选仍失败 |
| 35ed52da | 你能做什么？帮我查上海到北京的航班 | flight | model / flight | model / flight | 通过 |
| 35ed52da | 介绍一下你自己，并找附近的川菜馆 | place_discovery | model / clarification_only | model / clarification_only | 基线已失败，候选仍失败 |
| 7b96bf45 | 请介绍一下你自己 | direct | literal / direct | model / direct | 通过 |
| 7b96bf45 | 你是谁呀？ | direct | literal / direct | literal / direct | 通过 |
| 7b96bf45 | 你叫什么名字？ | direct | literal / direct | literal / direct | 通过 |
| 7b96bf45 | 你能做什么呢？ | direct | literal / direct | literal / direct | 通过 |
| a96b4469 | 你好，请问你是谁？ | direct | literal / direct | model / direct | 通过 |
| a96b4469 | 请问一下，你是谁？ | direct | literal / direct | model / direct | 通过 |
| a96b4469 | 可以介绍一下你自己吗？ | direct | literal / direct | model / direct | 通过 |
| a96b4469 | 麻烦你介绍一下你自己 | direct | literal / direct | model / direct | 通过 |
| a96b4469 | 你好，请问你是谁？然后查北京天气 | weather | failed / clarification_only | failed / clarification_only | 基线已失败，候选仍失败 |
| e3cfc9b1 | 能介绍一下你自己吗？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 能否介绍一下你自己？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 可以请你介绍一下你自己吗？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 可否告诉我你叫什么名字？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 是否可以请介绍一下你自己？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 能不能请问一下你是谁呀？ | direct | literal / direct | model / direct | 通过 |
| e3cfc9b1 | 能介绍一下你自己吗？然后查北京天气 | weather | failed / clarification_only | failed / clarification_only | 基线已失败，候选仍失败 |
| e3cfc9b1 | 能否介绍一下你自己？帮我查上海到北京的航班 | flight | model / flight | model / flight | 通过 |
| e3cfc9b1 | 可以请你介绍一下你自己吗？并找附近的川菜馆 | place_discovery | model / place_discovery | model / place_discovery | 通过 |
| e3cfc9b1 | 可否告诉我你叫什么名字？然后查北京天气 | weather | failed / clarification_only | failed / clarification_only | 基线已失败，候选仍失败 |
| e3cfc9b1 | 是否可以请介绍一下你自己？然后查北京天气 | weather | failed / clarification_only | failed / clarification_only | 基线已失败，候选仍失败 |
| 249ec024 | 可以先告诉我你是谁吗？ | direct | model / direct | model / direct | 通过 |
| 249ec024 | 麻烦你介绍一下自己吧，我还没用过这个助手。 | direct | model / direct | model / direct | 通过 |
| 249ec024 | 你是哪个团队做出来的呀？ | direct | model / direct | model / direct | 通过 |
| 249ec024 | 你觉得我适合做什么？ | direct | model / direct | model / direct | 通过 |
| 249ec024 | 你能告诉我怎么介绍自己才不尴尬吗？ | direct | model / direct | model / direct | 通过 |

## 结果边界

- 15 条纯身份原句当前 literal/direct 全部正确。候选保持其中 4 条 literal，其余 11 条全部实测 model/direct。
- 9 条复合原句：前后同为 3 条符合期望；其余 6 条基线已不正确（5 条 invalid_response，1 条模型直接选择 clarification_only）。不能写“历史原句全部通过”。
- 5 条独立 identity 前后全部 model/direct；两条负例的 expected_layer=model 自动通过。
- 旧复合测试 patch 了模型固定响应，证明委派与包解析链，不证明真实模型语义正确。
- 因存在5次invalid_response，29条没有可宣称的完整成功准确率；此处只按逐条行为记录通过/失败。
- 按确认范围仅实施核心句边界收窄，保留并单独跟踪这 6 条既有缺口；不改模型 prompt/解析器、MCP 或其他规则。

## 失败原始响应复核（真实调用，缓存状态未知）

天气5条均返回 `{"package_id":"mixed_itinerary","explicit_tool_names":["weather_forecast"]}`。契约要求 mixed_itinerary 为合法的多个工具族，因此拒绝并记 invalid_response；不是超时。川菜馆一条返回合法 `clarification_only` + 空工具。保留严格解析，不为了过测试放宽契约。原始输出见 failed-model-responses.stdout.jsonl。

## 本次处置

字面层只保留完整核心身份句的 fullmatch，不再递归剥离礼貌、情态、代词前缀。继续保留已证实能正确处理核心句的快速路径；对包装表达使用已有模型路径，11 条迁移原句均已实测判为 direct，调用耗时 411–1213 ms（包含路由调用开销，缓存状态未知）。没有证据要求把仍可靠的核心句也增加模型依赖，因此不整体退役身份分支。此取舍基于路由结果与职责边界，不基于行数。

这里的“收窄前后一致、无回归”指同一批输入的最终包结果一致；11 条纯身份原句的处理层从 literal 变为 model 是有意变化。新增模型依赖意味着这些包装句会承受原有 1.5 秒预算、模型故障和 fail-closed 行为；没有新增回退或放宽预算。

测试保留全部历史原句。对 6 条既有失败，固定本次真实模型响应并回放当前 parser/路由，断言其现状，不称其语义正确；录制响应回放也不等于新的模型实测。独立盲测集的原句、可接受包集合保持不变。

四个指定提交只提供新增回归出处，未取得最初 bug 报告原文。它们新增了 14 条纯身份句和 9 条复合句；基础“你是谁？”来自更早的 76402a7d。当前实现支持部分句末“吗/呀”，不能概括成这些语气词都会使命中落空。


## 最终提交复核补充（保留与首轮不同的观测）

最终实现提交 `90f58c1b` / `ROUTER_VERSION=2026-09-20.1`，容器临时目录运行，不修改部署中的 `/app`。实际源码哈希已保存。

- 完整 47 条：最终版与同环境重跑基线 `4678e3f0` 均为 **43/47**，逐条最终包完全相同。未命中为原三条 MCP 加 `weather-03`“上海今天空气质量怎么样”；不沿用之前 44/47。
- 其中 5 条 identity 全部正确，两个 `expected_layer=model` 负例通过。原 33 条 rules 仍 14/33；本轮原 33 条 hybrid 为 32/33，空气质量一条未达到原期望。
- 最终 29 条历史/独立身份复核：15 纯身份与 5 独立 identity 仍全部正确，但复合失败从首轮 6 条变成 7 条。新增变化句是“可以请你介绍一下你自己吗？并找附近的川菜馆”，首轮 baseline/core_only 都是 place_discovery，最终复核为 model/clarification_only。
- 对该变化句立即用原基线与最终版定点复核，两边均为 model/clarification_only，实际模型输入哈希同为 `ceb26495095f6be09c0725bcb7a15a483df59d4f5bf08c096f0a0beb6bc9a40c`。天气空气质量句两边同样为 model/clarification_only，输入哈希同为 `b9d86dadf06b36a63b354f6b25c250cf0e26bf211328c85a01fd1a1296b89f6a`。没有改变模型输入；不把跨批次模型输出变化归为已证实的收窄回归，也不隐去初轮和复核差异。
- 这些观察只支持本次成对比较与已覆盖路径；不能保证未来模型输出恒定。缓存状态仍 unknown，没有把重复调用宣称独立准确率。

六条首轮既有失败仍单列且录制回放固定；额外一条复合输出变化也补记 #107。原始输出：final-historical29.stdout.jsonl、current-baseline47.stdout.txt、final-hybrid47.stdout.txt、changed-baseline.stdout.jsonl、changed-final.stdout.jsonl 及各自 stderr/exit。

原始输出与逐条证据见 [PR #108 实测补充](https://github.com/HyxiaoGe/fusion/pull/108#issuecomment-5748235359)，新增跨批次复合变化见 [#107 补充](https://github.com/HyxiaoGe/fusion/issues/107#issuecomment-5748235486)。
