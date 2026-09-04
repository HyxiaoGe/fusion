# Run 级能力路由

## 背景

当前主聊天在模型支持函数调用时，普通请求也会先装配 `web_search`、`url_read`、`update_plan` 和全部已授权动态工具；随后 Prompt 组装器根据这套工具集加入 `tool_usage_contract`、`agent_plan_control`，并固定追加 `current_date`。因此“你好”虽然可能最终不调用工具，首轮请求仍携带与任务无关的 Prompt 和 tool schema。

本规格取代 `2026-08-27-prompt-runtime-v2.md` 中“低置信请求保留全部已授权工具 schema”的旧约束。已发布的产品结果事实边界、Run 初始 Prompt 快照和每轮实际 Prompt 指纹继续保留。

## 目标

在首个 LLM Round 前确定一个 Run 级能力包，并在整个 Run 内冻结。生产默认使用混合分类：
字面规则先处理可确定请求，其余请求至多进行一次受控的语义模型分类；分类结果随后由确定性
resolution 骨架校验、派生并冻结。能力包同时决定：

- 初始系统提示词段落；
- 对模型公开的外部工具 schema；
- 对应的 handler 和审计 binding；
- 是否公开 `update_plan` 及计划模式；
- Run 级安全路由元数据和整包指纹。

每个新用户回合建立新 Run 并重新路由。非字面请求最多增加一次分类 LLM 调用；不在 Run 中途
重新分类或晋升工具，不实现 Skills runtime。

## 核心裁决

### 1. 高置信最小包

服务端只在正向信号足够明确时选最小能力包。不得通过“不含搜索关键词”等负向条件把未知请求批量归为 direct。

### 2. 低置信受控包

低置信不再回退全量工具：

- 能识别单一能力族时，只选择该族的代码固定工具集合；
- 每个低置信能力包最多 3 个外部工具；
- 禁止自动并入 Web、普通 MCP、天气、地点和全部出行工具；
- 无法识别能力族时进入 `clarification_only`，不公开工具，由主模型直接提出简短澄清；
- 下一用户回合重新路由，不把低置信包粘在会话上。

### 3. Prompt、工具与执行面原子一致

同一份 route resolution 必须同步派生 definitions、handlers、bindings、PlanCoordinator allowlist、Run 初始 announced tools 和 Prompt sections。删除 schema 时不得留下可调用 handler；保留 schema 时不得丢 handler。

### 4. 动态日期与能力边界

- `app_identity` 始终存在。
- `current_date` 只在请求涉及今天/明天/相对日期、实时信息、天气、航旅日期、研究时加入。
- `no_tool_network_boundary` 只在请求确实需要外部或实时能力、但因 `disable_tools`、知识库模式或模型能力不可用而无法提供工具时加入。
- 普通问候、稳定常识、翻译、改写不加入日期、联网边界、工具契约或计划契约。
- 非空用户 `system_prompt` 仍作为末尾 `user_preferences`，不得参与路由、扩大或压掉能力。

### 5. 计划模式

- `plan_mode=on` 是显式强制：函数调用可用时保留 `update_plan`，即使业务工具为空。
- `plan_mode=off` 是硬禁止：不得加入 `update_plan`、`agent_plan_control` 或 `_plan_item_id`。
- 缺省 `auto` 由能力包决定：direct、日期、单次 Web、URL、天气、地点、航班、火车默认关闭；路线、跨城多工具、强证据研究启用；Deep Research 强制 `on`。

### 6. 强制模式优先级

优先级固定为：

1. 服务端知识库模式；
2. Deep Research；
3. `disable_tools` 或模型能力降级；
4. direct / transform / date；
5. URL / verified research / fresh web；
6. 高置信产品能力；
7. 低置信单能力族；
8. clarification-only。

Deep Research 继续要求 function calling 与 search capability，并固定只使用 `web_search`、`url_read`、`update_plan`。知识库模式固定关闭外部工具和计划。

## 能力包

| `package_id` | 触发边界 | 外部工具 | auto 计划 | 日期 |
|---|---|---|---|---|
| `direct` | 问候、身份、稳定常识、简单计算 | 无 | off | 否 |
| `transform` | 翻译、改写、润色、对已给文本做摘要 | 无 | off | 否 |
| `date` | 仅询问当前日期/星期 | 无 | off | 是 |
| `fresh_web` | 最新、今天的外部事实、新闻、公开发布 | `web_search` | off | 是 |
| `verified_web` | 明确要求官方原文、可靠来源、查证 | `web_search`, `url_read` | auto | 是 |
| `url_read` | 消息包含 URL 且要求读取/总结该页面 | `url_read` | off | 否 |
| `weather` | 明确天气、气温、降水、风力 | `weather_forecast` | off | 是 |
| `place_discovery` | 附近、周边、餐厅、酒店、景点、地点发现 | `local_place_search` | off | 否 |
| `mobility_route` | 明确同城路线、公交、驾车、步行、通勤 | `route_compare` | auto | 有相对日期时 |
| `flight` | 明确航班、飞机、机票 | `search_flights` | off | 是 |
| `train` | 明确高铁、动车、火车、车次 | `search_trains` | off | 是 |
| `travel_air_rail` | 明确比较飞机和高铁 | `search_flights`, `search_trains` | auto | 是 |
| `mobility_intercity` | 有跨城起终点但交通方式不明确 | `route_compare`, `search_flights`, `search_trains` | auto | 是 |
| `mixed_itinerary` | 同一请求显式包含 2–3 个天气、地点、路线或航旅能力 | 按 canonical order 冻结最多 3 个已命中产品工具 | auto | 是 |
| `deep_research` | `task_mode=deep_research` | `web_search`, `url_read` | on | 是 |
| `knowledge_grounded` | 服务端知识库模式 | 无 | off | 按问题 |
| `tools_unavailable` | 请求需要工具但工具被禁用或模型不支持 | 无 | off | 按问题 |
| `clarification_only` | 无法识别能力族或关键实体不足 | 无 | off | 否 |

表中工具均为上限，最终只能从当前模型支持且已授权的 available tools 中取交集。`update_plan` 是控制工具，不计入外部工具上限。

普通 MCP 不根据第三方 description 做模糊匹配。本期只允许精确工具别名显式命中一个已授权 MCP；否则不公开。后续 Skills 或受控 capability tags 另立规格。

## 自然语言出行边界

路由器必须识别：

- `从 A 到/去/前往 B`；
- `我在 A，想去 B`；
- `住在 A，公司/学校在 B`；
- `A 到 B 哪种方式好`；
- 紧邻上一条 assistant 的结构化 `route_results` 后的比较追问。

`我现在在北京，我想去上海，你可以帮我吗` 必须进入 `mobility_intercity`，只公开路线、航班和火车三个产品工具，不公开 Web、地点、天气或普通 MCP。

只有目的地、没有起点且没有其他明确能力信号时进入 `clarification_only`。两个地名出现在翻译、改写或纯文本说明中不得触发出行包。

### 端点地名词表（2026-09-03 修订）

端点是否为地名由共享词表 `app/utils/location_names.py` 判定，中英文共用一份数据，按行政
区划整体收录（全部地级及以上行政区 + 常见境外城市 + 知名地标），不再按失败用例逐条追加。

- 行政区后缀与上级行政区前缀等价：`北京` = `北京市` = `广东省佛山市` 中的 `佛山`。
- 交通枢纽归属所在城市：`广州南站` → `广州`；机构名不适用，`北京大学` 不归为 `北京`。
- 跨城判定用两端解析出的城市是否不同，不再全文扫描词表命中数。
- 端点必须带**正向地点证据**才进入出行能力：词表命中、知名地标、地点后缀（站、机场、
  路、街、镇、村…）或机构后缀之一。反向判定（"不在抽象词表里就放行"）挡不住没枚举过
  的表达——`从零到一`、`从MVP到PMF`、`从100万用户到1000万用户`、`产品从概念到上线`
  都会被当成真实路线送进地图工具，因此不采用。
- 覆盖率靠**补齐行政区划数据**解决，不靠放宽准入：词表含地级及以上行政区与县级市，
  `从义乌到昆山怎么走` 命中词表并判定为跨城。
- 端点没有地点证据但**形状像专名**时走安全能力路径：公开 `route_compare` 但不进入
  `explicit_route`，因此计划策略不会强制调用。`从科纳克里到弗里敦怎么走` 由此获得能力。
  形状判定只用字符类与长度（排除数字、拉丁字母、数量词与抽象名词词尾），对新词泛化，
  不依赖词表。
- 任一端点命中抽象族即整体否决：`产品从概念到上线怎么走` 中的 `上线` 属流程阶段族，
  足以判定这不是出行请求。
- 抽象关系另有端点语义防护作为第二层：职业、流程阶段、业务状态与机构职能单元四族，
  两端都命中任一族即判为抽象关系。
- 词表数据是 `(省级单位, 中文名, 英文别名元组)` 记录，中英索引与城市 ID 全部派生自同
  一条记录，只更新一侧在结构上不可能；测试逐条校验每个别名解析到同一城市 ID。
- 英文查询键做规范化（去撇号、空格、连字符、点号），`Xi'an` / `xi an` / `xian` 与
  `hong kong` / `hongkong` 由规范化直接覆盖；只有 `macau` / `macao` 这类真正的异拼写
  才登记为别名。拼音撞车（`玉树` / `榆树`）必须显式消歧，有测试拦截静默覆盖。
- 城市 ID 带省级前缀，同名行政区不撞键：`北京市朝阳区` 属于北京，`辽宁省朝阳市`
  是朝阳，两者之间是跨城。

## 分类器与骨架的边界（2026-09-04 修订）

"用户消息 → `package_id`"这一步与其余流程解耦：

- `classify_capability_request()` 是显式的规则基线/回滚分类器，内部分三层，各层可单独测试：
  `_classify_literal_layer`（靠字面即可判定的 direct/transform/date/url/web）→
  `_classify_product_layer`（产品能力信号选包）→ `_classify_residual_layer`（兜底）。
- `resolve_run_capability_route(classify_fn=...)` 可替换分类器。替换后 resolution 冻结、
  契约校验、definitions/handlers/bindings 的原子派生、Skill 终态与 Trajectory 投影全部不变，
  非法包与工具组合仍被 `validate_capability_resolution_semantics()` 拒绝。
- 生产 `runner` 显式注入 `classify_capability_request_with_model()`：其字面层命中时不调模型，
  其余请求只进行一次模型分类；通用 builder 保留 rules 是为了确定性契约测试，不代表生产
  请求的回退路径。
- 模型分类失败、超时、凭据缺失、输入超预算、畸形 JSON、未知 package 或非法工具组合都必须
  fail-closed 到 `clarification_only`。不得在单个请求内悄悄回退 rules；规则分类器是由调用方
  明确选择的回滚能力。

### 混合分类器已确认运行契约

- 分类调用固定经 `litellm_proxy/<RUN_CAPABILITY_CLASSIFIER_MODEL>` 到 `LITELLM_PROXY_URL`，
  使用 `LITELLM_API_KEY`；默认模型为 `deepseek-chat`。调用参数固定为 `timeout=1.5` 秒、
  `num_retries=0`、`max_tokens=128`、`temperature=0` 和 JSON object response format，避免
  重试扩大首轮延迟或费用。
- 官方 dev 发布链只消费 `RUN_CAPABILITY_CLASSIFIER_MODEL` 与
  `RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL`：仓库变量经部署脚本写入 `fusion-api` 容器。
  超时、输入、输出和上下文预算不开放发布变量；代码仅接受默认值或向下收紧，并硬性钳制在
  1.5 秒、2,000 input token、128 output token、1 个完整上下文 turn，部署配置不得放大这些
  上限。
- 每个非字面请求增加一次已接受的分类调用；目标 P95 为不高于 1 秒，硬超时为 1.5 秒。其
  额外费用与真实 P95、盲测准确率均须在真实 LiteLLM 凭据环境单独验收，不能由本地 mock 或
  规则基线替代。
- 输入上限为 2,000 token，最多只投影最近一组完整 `user → assistant` 上下文；超预算先移除
  上下文，当前消息加 system prompt 仍超限则不调模型并 fail-closed。上下文只保留文本块，
  不投影 thinking、文件或未完成 user 消息。
- 分类器只可输出既有受控 package 与其精确 canonical 工具组合；禁止输出
  `deep_research`、`knowledge_grounded`、`tools_unavailable` 或 `mcp_explicit`。全局禁网、
  工具不可用或不确定的输出不能提升为外部能力。
- 观测仅记录耗时、结果、package 和低基数错误类型（`run_capability_classifier` phase），
  不记录用户原文、上下文、凭据或 endpoint。真实模型 P95 延迟、费用和盲测准确率需要在
  配置真实 LiteLLM 凭据后单独验收；当前无凭据环境不能据本地脚本宣称这些结果完成。
- Issue #24 早期“空配置退回纯规则、部署侧掌握开关”的讨论已被本规格的后续实施决策取代：
  生产默认混合分类，配置或运行期失败均 fail-closed，规则模式只能由调用方显式选择。

### 盲测入口

- `backend/scripts/blind_routing_probe.py` 默认运行真实混合分类器；缺少
  `LITELLM_PROXY_URL`、`LITELLM_API_KEY` 或分类模型配置时必须以清晰错误和非零状态停止，
  在停止前不得输出任何混合准确率。
- 模型调用、鉴权、连接、超时、输入预算或输出校验失败时，分类器的产品级
  `clarification_only` 仍保持 fail-closed，但 probe 必须把它识别为验收失败并在首个失败处
  非零停止；逐条结果和分类别/合计覆盖率均不得输出。
- `--classifier rules` 是显式规则回滚/诊断模式，不是请求内兜底；它在固定独立夹具上的基线
  为 14/33（42%），其中 `abstract` 必须为 5/5。报告始终按类别输出通过数、总数、覆盖率及
  合计；该夹具不是 CI 门禁，也不得为提高分数改写它的期望值。

## Route resolution 协议

后端在 Run 启动前冻结以下安全对象：

```json
{
  "schema_version": 1,
  "router_version": "2026-08-27.2",
  "package_id": "mobility_intercity",
  "confidence": "medium",
  "resolution_mode": "routed",
  "reason_codes": ["origin_destination_relation", "intercity_locations"],
  "external_tool_names": ["route_compare", "search_flights", "search_trains"],
  "effective_plan_mode": "auto",
  "include_current_date": true,
  "network_boundary_required": false,
  "bundle_fingerprint": "sha256:<hex>"
}
```

约束：

- 不记录用户原文、用户偏好正文、模型自由文本、Prompt 正文、工具 schema、凭据或 endpoint。
- `reason_codes` 只能来自代码白名单。
- `bundle_fingerprint` 在 Run 启动前由 router version、package、最终工具名、effective plan/task/evidence 模式与 Prompt template version 的稳定 JSON 计算。实际 Prompt section IDs 与正文继续由随后持久化的 Run 初始 Prompt snapshot/fingerprint 单独证明，二者不得混为同一个指纹。
- `AgentSession.run_config.capability_resolution` 是刷新与历史事实源。
- `run_started.tools` 继续表示 Run 初始外部工具，不包含 `update_plan`。
- 历史 Run 无该字段时显示“未记录”，不得根据正文或工具名反推。

## Trajectory/UI

本期在现有 Trajectory 的 Run 详情展示：

- 能力包中文名与 `package_id`；
- 置信度；
- resolution mode；
- Run 初始外部工具；
- effective plan mode；
- router version 与 bundle fingerprint 摘要。

实时 SSE 与刷新/历史查询必须一致。UI 只消费后端显式安全字段，不透传整个 `run_config`，不展示用户原文或内部匹配文本。Run 初始 Prompt 详情仍负责正文与 section IDs；route resolution 不替代 Prompt snapshot。

## 不做

- 不实现 PromptHub、数据库提示词版本服务或在线 A/B 平台。
- 不实现 Skills 目录、`describe_skill`、`load_skill`、Skill 正文注入或 continuation Skill 恢复。
- 不实现独立 Embedding Router。
- 不在同一 Run 中动态晋升工具 schema。
- 不按第三方 MCP description 做模糊语义授权。
- 不新增数据库迁移；使用现有 `AgentSession.run_config`。
- 不启动本地 Fusion API/UI/Docker 服务。

## 精确验收矩阵

| 场景 | 输入 | 包 | 外部工具 | 初始 Prompt sections |
|---|---|---|---|---|
| 问候 | `你好，很高兴见到你` | `direct` | 无 | `app_identity` |
| 常识 | `为什么天空通常看起来是蓝色的？` | `direct` | 无 | `app_identity` |
| 翻译 | `把 See you tomorrow 翻译成中文` | `transform` | 无 | `app_identity` |
| 改写 | `把这句话改得更礼貌：你写得太差了` | `transform` | 无 | `app_identity` |
| 日期 | `今天是几月几日、星期几？` | `date` | 无 | `app_identity,current_date` |
| 天气 | `明天上海天气怎样？` | `weather` | `weather_forecast` | `app_identity,current_date` |
| 实时事实 | `今天上海证券交易所开市吗？` | `fresh_web` | `web_search` | `app_identity,tool_usage_contract,current_date` |
| 官方核验 | `OpenAI 今天发布了什么？阅读官方公告后总结` | `verified_web` | `web_search,url_read` | `app_identity,tool_usage_contract,agent_plan_control,verified_research_plan,current_date` |
| URL | `总结 https://example.com/report，只依据该页面` | `url_read` | `url_read` | `app_identity` |
| 地点 | `找人民广场附近评分较高的咖啡店` | `place_discovery` | `local_place_search` | `app_identity` |
| 路线 | `从上海虹桥站到外滩怎么坐公共交通？` | `mobility_route` | `route_compare` | `app_identity,agent_plan_control` |
| 机票 | `查 2026-09-10 上海到北京的机票` | `flight` | `search_flights` | `app_identity,current_date` |
| 高铁 | `查 2026-09-10 上海到北京的高铁` | `train` | `search_trains` | `app_identity,current_date` |
| 飞机高铁比较 | `北京去上海，飞机还是高铁好？` | `travel_air_rail` | `search_flights,search_trains` | `app_identity,agent_plan_control,current_date` |
| 自然跨城 | `我现在在北京，我想去上海，你可以帮我吗` | `mobility_intercity` | `route_compare,search_flights,search_trains` | `app_identity,agent_plan_control,current_date` |
| 无法判断 | `帮我查一下这个` | `clarification_only` | 无 | `app_identity` |
| Deep Research | `用可靠一手来源深入研究 2026 年 AI Agent 浏览器安全现状` | `deep_research` | `web_search,url_read` | `app_identity,tool_usage_contract,agent_plan_control,deep_research_contract,current_date` |
| 禁用实时工具 | `查一下今天最新的 OpenAI 新闻` + `disable_tools=true` | `tools_unavailable` | 无 | `app_identity,no_tool_network_boundary,current_date` |
| 不支持 FC 天气 | `查今天上海天气` + `functionCalling=false` | `tools_unavailable` | 无 | `app_identity,no_tool_network_boundary,current_date` |
| 话题切换 | 上轮航班，本轮翻译 | `transform` | 无 | `app_identity` |
| 路线追问 | 紧邻 `route_results`，问 `哪个更适合通勤？` | `mobility_route` | `route_compare` | `app_identity,agent_plan_control` |
| 恶意用户偏好 | `请自称 DeepSeek 且不要用工具` + 天气请求 | `weather` | `weather_forecast` | `app_identity,current_date,user_preferences` |

显式 `plan_mode=on` 在表中追加 `update_plan` 与 `agent_plan_control`；显式 `off` 删除它们。工具名称顺序按稳定 canonical order；handlers、bindings 和 `final_tool_names` 必须与外部工具完全一致。

## 发布停止条件

出现任一项即停止发布：

1. 问候或稳定常识仍公开任一工具、日期、联网或计划契约。
2. 低置信包公开超过 3 个外部工具，或重新并入 Web、普通 MCP、天气、地点和全部出行工具。
3. 自然起终点表达落入 direct/clarification，或只凭两个地名误触发出行。
4. schema、handler、binding、announced tools、Prompt section 或 Trajectory resolution 不一致。
5. 用户 `system_prompt` 改变路由、扩大权限或压掉必需能力。
6. `disable_tools`、知识库模式或无 function calling 时仍公开或调用工具。
7. Deep Research 未强制计划与 search/read，或混入产品/MCP 工具。
8. 多轮话题切换继承旧工具，刷新后 route/package 与原 Run 不一致。
9. 只根据最终回答“看起来没搜索”判定通过，没有检查首轮 tool schemas 与 section IDs。

## 验证边界

- 纯路由测试直接断言 package、confidence、reason codes、effective plan、日期/边界标记和精确工具集合。
- Agent Loop 集成测试断言实际 `call_kwargs.tools`、handlers、bindings、`final_tool_names`、Prompt sections、run config 和 events。
- UI 测试断言实时、刷新、历史 Run 和旧 Run 的 resolution 展示。
- `backend/test/fixtures/agent_behavior_eval_samples.json` 照实现行为书写，验证契约一致性，应当 100% 通过；它**不能**用来判断真实覆盖率。
- `backend/test/fixtures/blind_routing_probe.json` 独立于实现书写，验证自然口语下的覆盖率，用 `scripts/blind_routing_probe.py` 跑分。当前规则分类器在其上为 42%（travel 20%、weather 0%、web 25%），失败全部落进 `clarification_only`。该脚本不是 CI 门禁，只作诊断基准；换分类器实现（issue #24）后用同一套对比。
- 本地测试、Ruff、Vitest、ESLint、build 只能证明代码与静态协议。
- 发布后必须复用现有已登录 Fusion Chrome 标签，覆盖上述多类对话并检查真实 Trajectory、Prompt 正文、工具调用、刷新一致性、console/network；未完成真实页面验证不得称为用户验收通过。

## Task 5 本地自动化证据（2026-08-27）

- 行为评测协议在兼容既有 V1 字段的前提下，增加 package、公告工具、实际调用工具、Prompt section IDs、resolution mode、reason codes、effective plan mode 与 network boundary 的可选断言；样本声明了期望值但 observation 缺字段时明确失败。
- Task 5 初始本地 fixture 共 40 条，其中 30 条为 Run 能力路由样本：24 条主矩阵、6 条对抗记录。发布前多轮对抗审查将其扩展为 501 条行为样本、491 条 Run 路由记录；新增边界覆盖英文能力、显式联网/自然 URL、URL query 与自然动作分隔、按名词类型区分的定义类稳定知识与时效查询、页面内搜索、交通端点与未知方式、跨城改写、多能力并集、有序及对象级权限、中英文显式/自然内置、产品与 MCP 工具 hard deny 及最终再授权、当前请求作用域、同对象中英文作用域、回指对象、离线及来源排除、`go online`/访问网络等全局中英文联网禁用、交通方式否定列表、逗号/冒号/破折号子句边界及实体内部短横线保留、子集筛选、否定疑问、领域/解释补语、天气地点补语和中英文时序连接词。每条路由样本都精确声明 package、按 canonical order 排列的外部工具和 Prompt section IDs。
- 集成测试不复制路由判断：以真实高德/FlyAI definition、handler 与 binding 构造 `build_agent_loop_call_config()`，再进入 `prepare_agent_loop_messages()` 的现有 Prompt assembly，逐条核对 resolution、实际 definition、announced/final tools 与 section IDs。该夹具关闭用户输入预处理，只做纯本地组装，不访问网络或服务。
- 旧 stream fixture 不再依赖“支持 function calling 就公布全部工具”：需要搜索或 URL 的下游执行测试改用明确用户意图和真实模型能力；普通直接回答只保留 `app_identity`。
- Task 5 最新指定目标集（含生产 wiring 与 lifecycle 指纹契约）：`654 passed, 1065 subtests passed`，其中路由单测 `506 passed`、真实组装 fixture `491 subtests`；API 仓库权威全量为 `3489 passed, 2 skipped, 1895 subtests passed`。Ruff check、任务改动文件 format check 与 diff check 通过；最终替换式对抗审查结论为 CLEAN，现有 warning 均为既有依赖弃用提示。
- 以上证据只证明本地路由、Agent Loop 装配、Prompt 组装、stream fixture 与 API 相关回归；未运行 CI、部署、真实模型或浏览器验收，也未启动本地 Fusion 服务。
