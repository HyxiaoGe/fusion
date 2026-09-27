# 语义正则迁移第十一阶段：产品回答校验只保留形态检查

## 两次改动

- PR [#160](https://github.com/HyxiaoGe/fusion/pull/160)（`1182debd`）删除回答侧“提到风险/费用词且没有免责措辞就拦”的规则和用户原文残留判断，改由工具结果 `limitations` 与 `amap.final_answer`、`flyai.fact_boundary` 使用约束提示词前置约束回答边界。锚点机制另开 issue [#161](https://github.com/HyxiaoGe/fusion/issues/161)。
- #160 发布后首轮 dev 真实请求 7 次里 5 次被替换成确定性兜底，全部是误判：咖啡馆空位 ×2 与火锅排队命中 `unknown_place`（“推荐先打电话确认有没有位子”），晚班航班命中 `unknown_travel_time`（“18:00 以后起飞的晚班”），航班余票命中 `unknown_travel_entity`（“从深圳宝安国际机场出发”）。根因是 validator 用正则从自然语言回答里抽地点、时刻、机场、班次、数字，再猜指向哪条结果。
- 曾提出用“以后/之前”等词做放行、剥离城市前缀的补丁，被否决：问题本身就是正则在猜语义，任何解决方案都不应再做关键词匹配。
- PR [#162](https://github.com/HyxiaoGe/fusion/pull/162)（`18a3d517`）把 `product_answer_validator.py` 从 2000+ 行缩到 54 行，只检查空回答、Markdown 表格、缺少产品结果块；删除默认关闭的正则切句改写层 `repair_unsupported_product_answer` 与 `PRODUCT_ANSWER_REPAIR_ENABLED`；观测去掉 `repair_*` 字段，迁移 `c4e8a2f1d935` 删除观测表 4 个 repair 列。本地 21 个相关测试文件 462 项通过，PR CI 与 dev 发布成功，dev 迁移头为 `c4e8a2f1d935`。

## 复验结果

dev 运行 `18a3d517`，原有 Chrome 登录态逐条新建会话发送，每题 3 次，共 21 次有效 run（另有 1 次因输入工具吞掉数字作废）。全部由模型直接交付，没有一次被兜底替换。

| 用例 | 问句 | run | 结论 |
|---|---|---|---|
| P1 | 深圳南山科技园附近的咖啡馆，现在过去有空位吗？ | `c4a5ec7c02f3494fad00d220df85d730`、`75b5b182150d40c1bd876daeb5b728f4`、`c041b13c855541bb883758eed5ea5991` | 通过；后两次正文逐字相同 |
| P2 | 帮我找福田附近的火锅店，晚上去不用排队吧？ | `e9f4e45b2fab4a5880461cb8ba37b96d`、`f40e1e9dd356435caca08b43ecb4f20a`、`224844df3d1242f5b586e41917b6230d` | 通过；后两次正文逐字相同 |
| P3 | 深圳海岸城附近的烤肉店，随时去都能进吗？ | `43ebcf2a7cf149a48178f95456a58e24`、`0fb0394b58244a3abd55da9a4d9b3e63`、`2becc271ed4e47548ee8091033bb6bfd` | 通过 |
| F1 | 10月10日从深圳飞北京的航班有哪些？坐晚上的航班能省一晚住宿费吗？ | `318982a8b4e24308a61d01819afb925d`、`798dcc0f035c44f7b17a5d28b9629b71`、`cae8d6accdb5479293d823c234f654e5` | 边界通过；首次有事实错误，见下 |
| F2 | 10月10日深圳到北京的航班还有票吗？ | `788ef0b4e6514118b659afbb16966ebb`、`d98131e0753f4952b9f4520114254830`、`b4974a78836947d684d3c8e5273f0d45` | 通过；均说明余票查不到 |
| F3 | 10月10日深圳飞北京的航班，哪班准点率高？ | `abae27a1617a4116bbd59ce5ae70afcb`、`061db33b47bb4c978b1060db9522b1a8`、`71faf95304f84236899e5f997fcc1a53` | 通过；准点率来自 web_search 并注明第三方统计 |
| R1 | 从深圳北站开车去深圳湾公园要多久？过路费多少？ | `84479623a05b45bba08a6d2dc92654d7`、`e7827a664a54495dbb57097cc0088c1f`、`81714577f9eb46478cdf548a6c10941e` | 通过；均说明过路费未返回 |

- 除 F3 后两次外，轨迹末轮均为 `source=model, disposition=emitted`。F3 后两次的 API 日志为 `PRODUCT_ANSWER_VALIDATION is_valid=true reason_code=ok`，`agent_sessions.status=completed`，但轨迹账本降级（见观察项 1），`agent_events` 缺少末轮与 `run_completed`。
- 作废 run `765f2758c1334303b366bf9b9e3dd4f3`：浏览器输入工具把“10月10日”吞成“月日”，模型追问日期、未调用产品工具，按设计走无产品结果兜底 `source=server, reason=product_guard`，不计入样本。

## 观察项

1. **轨迹账本 `admission_full` 静默丢事件。** F3 带 web_search/url_read，F3-2 一轮推出 85 条 evidence 事件，打满进程级 4 名额非阻塞准入信号量，账本降级后续事件全丢，事件停在 seq 179/196，`admission_full` 分支不打日志。近 7 天 `run_trajectory_meta`：complete 172、admission_full 11、recorder_timeout 3、finalize_mismatch 1。与本次校验改动无关，需单独处理。
2. **F1 首次回答有数值事实错误。** run `318982a8…` 称“MU6670 ¥523 是这批里唯一低于 ¥600”，同批 MF8350 ¥595、CZ3155 ¥580 也低于 600。旧数值正则可能拦得住这类错，但它同时造成大量误判；形态校验下这类错误只能靠提示词与后续观测。
3. **边界措辞。** P2-1“错峰去理论上更从容”后接“无法确认”；F1-3“并不直接减少一个需要付费的夜晚”属反向推断；F2-2“只要还有座位就可以订”为泛化表述；R1-2“大多是市政道路和城市快速路”“周末周边车流较大”为通用常识。均未断言结果外事实，记录备查。
4. **首轮 R1 的 `route_compare` 曾返回高德 `invalid_response`**；本轮 3 次均成功（约 2.7—2.9 秒）。
5. **兜底替换时模型原文未持久化**，被替换的候选无法事后复核，只剩原因码。
