# Run 内能力升级

状态：已实施（2026-10-04），待 dev 验收。实施时对第 1、2、4 节的调整见各节「实施说明」。

## 背景

Run 级能力路由（`2026-08-27-run-capability-router.md`）在首个 LLM Round 前把请求分进一个能力包，并在整个 Run 内冻结。模型只能使用该包授权的工具，判错无法在本 Run 内补救：

- 「周末带孩子在上海玩一天，怎么安排比较好」判为 `direct`，不能查天气或地点，trip-planning 也不进目录。2026-10-03 dev 对照：同类行程问题判为 `mixed_itinerary` 的 10/10 加载 trip-planning 并调用工具；判为 `direct` 的 5 条全部只靠模型知识作答。
- 「特朗普降半旗」在 2026-09-28 连续 6 次判为 `clarification_only`，用户被反复要求澄清。

现在的做法是持续打磨分类提示词的边界条件。分类器面对的是用户原文，而作答模型更清楚自己缺什么事实；边界误判不会被提示词穷尽。

## 与既有裁决的关系

- **推翻**：路由规格「不在 Run 中途重新分类或晋升工具」与「不在同一 Run 中动态晋升工具 schema」。理由同上；本设计用受控的一次升级替代「永远冻结」，其余冻结语义（Prompt/工具/执行面原子一致、安全 resolution 字段、指纹）保持。
- **保留**：路由器仍是每个 Run 的首判和快速路径。命中的请求零额外开销。
- **复用**：动态工具发现原型（`dynamic-tool-discovery-*.md`，默认关闭）已经实现运行中扩容时同步 schema、handler、MCP binding、计划允许集与公告工具，以及延期提交与无证据守卫。本设计复用这套运行态同步，但不采用它「跳过路由器、只给 `tool_search`」的入口：那条路径要求每个 Run 都先做一轮发现，且未经真实模型验证。两者长期可以收敛，本期不合并。

## 目标

1. 路由给出的能力不足时，作答模型可以在同一 Run 内申请一次升级，服务端校验后切换到目标能力包继续执行。
2. 是否申请完全由模型判断。服务端不做关键词、正则或语义规则匹配，只做授权与状态的硬校验。
3. 升级后的 Run 与「首判就是目标包」的 Run 在工具、Prompt、计划、证据守卫、Skill 目录上等价。
4. 轨迹、SSE 与历史读回都能看到升级发生在哪一步、从哪个包到哪个包。

## 非目标

- 不降级、不在已有外部工具的包之间平移。
- 不改变路由器本身的分类逻辑和提示词。
- 不支持深度研究、知识库模式、`disable_tools`、续跑 Run 与文档交付轮。
- 本期不开放 `mcp_explicit` 作为升级目标（需要别名选择，单独设计）。

## 设计

### 1. 控制工具 `request_capability`

挂载条件（全部满足）：

- 模型支持函数调用；
- 首判包属于可升级来源：`direct`、`clarification_only`；
- 非深度研究、非知识库模式、未 `disable_tools`、非续跑、非文档交付模式；
- 开关 `RUN_CAPABILITY_ESCALATION_ENABLED` 打开；
- 本 Run 尚未升级过。

参数与分类器输出同构，便于复用同一套确定性 resolution 校验：

```json
{
  "package_id": "mixed_itinerary",
  "tool_names": ["weather_forecast", "local_place_search"],
  "primary_tool_name": "weather_forecast",
  "reason": "Needs this weekend's forecast and real places in Shanghai."
}
```

- `package_id` 的 enum 只列本 Run 可升级的目标包；`tool_names` 按包规则校验，省略时取包的固定工具集。
- `reason` 限长，只进审计，不参与任何判定。
- 工具描述用英文、说明用途和代价（多一轮、更慢），不写语义触发条件（不写「当用户提到天气时」之类）。
- 与 `update_plan`、`load_skill` 一样属于控制工具：不绑定计划步骤，不计入外部工具预算。

### 2. 服务端校验

按顺序，任一失败即拒绝。拒绝时返回结构化工具结果，模型继续用当前能力作答：

1. 本 Run 未升级过；
2. 目标包在可升级目标集合内；
3. 首判时的用户约束不放宽：`network_policy` 与 `denied_tool_names` 继承首判结果，模型不能覆盖；用户明确禁止联网时，联网类目标直接拒绝；
4. 目标包工具对当前模型与账号可用（函数调用、搜索能力、产品工具配置）；
5. 交给现有 resolution 骨架做与首判完全相同的派生校验（工具数量、primary tool、计划模式等）。

实施说明：

- 校验与首判共用 `build_candidate_route`，切换用固定候选重新调用 `build_agent_loop_call_config`，因此升级后的执行面与「首判就是目标包」逐字段相同（有离线等价测试）。
- 每个 Run 最多成功升级 1 次、申请 2 次（含被拒）；模型计划制定后申请一律拒绝（`plan_already_created`），避免计划与新工具集错位。

### 3. 原子切换

校验通过后，用目标 resolution 重新派生本 Run 的执行面，规则与 `build_agent_loop_call_config` 首判路径一致：

- `call_kwargs.tools`、handlers、MCP bindings、公告工具；
- 系统提示词段落：工具契约、`current_date`、计划控制、`skills_catalog` 等按新包重新组装，从下一轮起生效；原段落不再出现。发现原型的 promote 不处理 Prompt，这部分需要新补；
- 计划模式按目标包的 effective plan mode 重新确定；若为 `on`，从升级后的下一轮开始走「先制定计划」；
- 证据策略与无证据守卫按目标包；
- Skill 目录按新工具集重算，例如升级到 `mixed_itinerary` 后 trip-planning 出现。

升级前已产生的文本、思考与工具记录保留在消息历史中。若升级前已经流出正文，不回撤，后续轮次接着输出。

### 4. 协议与持久化

- `run_started.capability_resolution` 保持为首判结果，不改写。
- 新增事件 `capability_escalated`：

  ```json
  {
    "type": "capability_escalated",
    "protocol_version": 2,
    "step_number": 1,
    "from_package_id": "direct",
    "capability_resolution": { "...与 run_started.capability_resolution 同 schema、同指纹算法的目标包安全对象...": "" },
    "section_ids": ["app_identity", "current_date", "tool_usage_contract"],
    "system_prompt_fingerprint": "<64 位 hex>"
  }
  ```

  事件不含模型 `reason`；`reason` 只在该次工具调用的参数与结果里。

实施说明（相对原设计的调整）：

- 目标 resolution 保持正常的 `routed` 形态，不新增 `resolution_mode = "escalated"`：升级与否由事件本身表达，resolution schema 与前端校验不变。
- 不写 `AgentSession.run_config`。`capability_escalated` 是持久化轨迹事件，实时 SSE 与历史读回走同一条账本，事件本身就是事实源；`run_config` 保持首判快照。
- Run 级系统提示词快照（`system_prompt_prepared` 与详情）保持首判版本；升级后每轮的实际提示词由 `llm_round_started.system_prompt_fingerprint` 与 `tool_names` 区分，事件里的 `section_ids` 与指纹对应升级后的版本。
- 拒绝不发事件，只体现在该次工具调用的结果与日志里。
- 前后端协议同步：前端 normalizer 接受新事件；轨迹 Run 概览显示「首判包 → 升级包（第 N 步）」；工具注册表加 `request_capability` 节点（标签走 i18n）。必须与后端同一 PR 发布。

### 5. 观测

升级事件发出时打日志，并在后台路由质量面板增加：

- 各模型、各首判包的升级率与拒绝率；
- 升级后的外部工具调用次数与耗时；
- 首判 `direct` 且未升级、但回答被用户追问「查一下」之类的会话，作为漏升级样本人工抽查。

升级率是校准信号：过高说明分类器或工具描述需要调整，过低说明模型不愿申请。只调描述与提示词，不加代码规则。

## 验收

dev 部署后用探针账号逐条新会话运行，判定依据为轨迹事件与工具调用日志：

| 类别 | 用例 | 期望 |
|---|---|---|
| 应升级 | 周末带孩子在上海玩一天，怎么安排比较好 | `direct → mixed_itinerary`，随后调用天气/地点 |
| 应升级 | 上海三天怎么玩 / 周末在上海玩两天，怎么安排 | 升级到含地点工具的包 |
| 应升级 | 特朗普为啥宣布全美降半旗一周？（若首判为 direct 或澄清） | 升级到 `fresh_web` |
| 不应升级 | 你好 / 写一首关于秋天的短诗 / 解释一下 Rust 的所有权 | 无 `request_capability` 调用 |
| 拒绝 | 「不要联网，告诉我今天的新闻」 | 申请联网被拒，回答说明无法联网 |
| 回归 | 路由评估全集、盲测门禁 | 首判结果不变 |

另需离线测试覆盖：切换后 schema/handler/bindings/计划允许集/Prompt 段落一致；重复申请被拒；深度研究与知识库模式不挂工具；续跑不挂工具；升级前已流出正文时的落库形状；SSE 实时与历史读回事件一致。

## 发布与回退

- 配置开关 `RUN_CAPABILITY_ESCALATION_ENABLED`，dev 默认开启；关闭即恢复「首判冻结」，不影响已完成的历史 Run 读回（前端仍能渲染已有 `capability_escalated` 事件）。
- 发布停止条件：任一「不应升级」用例出现升级；或升级后出现未授权工具执行；或轨迹读回与实时不一致。

## 实施拆分

1. 后端：控制工具、校验、原子切换（复用发现原型的运行态同步，补 Prompt 重组）、事件、开关、测试。
2. 前端：事件归一化、轨迹概览、工具节点与 i18n。与 1 同一 PR。
3. 观测：路由质量面板指标，可单独 PR。
4. dev 验收与描述调优，结果补记到本文末尾。
