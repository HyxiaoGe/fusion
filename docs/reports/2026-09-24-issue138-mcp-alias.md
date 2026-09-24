# #138 MCP 别名模型分类：同轮配对证据（2026-09-24）

## 范围与证据层

- **代码阅读**：`75e0eee0` 的模型分类可选包排除了 `mcp_explicit`，提示词还明确禁止选择它；分类输入没有展示已授权 MCP 别名。`mcp_alias-01/02/04` 因此会落到 `clarification_only`。候选让模型看到本轮授权别名，允许选 `mcp_explicit`，再由服务端核验别名形状与当轮可用集合。
- **容器真实模型实测**：同一个 dev `fusion-api` 容器内，基线 `/app` 与候选 `/tmp/issue138-candidate` 交错执行，调用实际 LiteLLM 分类模型。没有切换运行中的 API，也没有创建页面会话。这是分类与能力边界实测，不是页面执行或 MCP 调用完成证明。
- **源码对应性**：基线分类器与 Git `75e0eee0` 的逐行内容一致；镜像是 CRLF，统一 LF 后 SHA-256 都为 `263f034238af72c6fb7c11e612e0e66ee7564f45dce68433ed1f5b6710ecd910`。候选分类器 SHA-256 为 `a3518ab8fbab1dbb7087bf185d34a72a8daf622a2a59d0a031d313cf98643f90`；候选提示词为 `251deb6b9b6c2c67e7d1c650955a17fd782c913cff41cb0db6e69c527631c01c`，本地与 dev 临时目录均一致。每条结果还检查了 Python 实际导入路径。首次取数脚本会把候选臂错误导回 `/app`，该次数据已作废，不参与以下结论。
- **数据口径**：下表是最终候选在同一轮交错取得的原始 `blind_routing_probe.json` 47 条。每格为 `layer / package / include_current_date / error`，`-` 表示没有错误。可接受包仍按原 fixture 判断；不引用历史评分。

| ID | 基线 | 候选 | fixture 判定 |
| --- | --- | --- | --- |
| travel-01 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | 通过 → 通过 |
| travel-02 | model / mobility_route / true / - | model / mobility_route / true / - | 通过 → 通过 |
| travel-03 | model / flight / true / - | model / flight / true / - | 通过 → 通过 |
| travel-04 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | 通过 → 通过 |
| travel-05 | model / mobility_route / true / - | model / mobility_route / true / - | 通过 → 通过 |
| travel-06 | model / mobility_route / true / - | model / mobility_route / true / - | 通过 → 通过 |
| travel-07 | model / clarification_only / false / - | model / clarification_only / false / - | 通过 → 通过 |
| travel-08 | model / clarification_only / false / - | model / clarification_only / false / - | 通过 → 通过 |
| travel-09 | model / train / true / - | model / mobility_intercity / true / - | 通过 → 通过 |
| travel-10 | model / train / true / - | model / train / true / - | 通过 → 通过 |
| weather-01 | model / weather / true / - | model / weather / true / - | 通过 → 通过 |
| weather-02 | model / weather / true / - | model / weather / true / - | 通过 → 通过 |
| weather-03 | model / fresh_web / true / - | model / fresh_web / true / - | 通过 → 通过 |
| place-01 | model / place_discovery / false / - | model / place_discovery / false / - | 通过 → 通过 |
| place-02 | model / place_discovery / false / - | model / place_discovery / false / - | 通过 → 通过 |
| direct-01 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| direct-02 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| direct-03 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| direct-04 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| transform-01 | model / transform / false / - | model / transform / false / - | 通过 → 通过 |
| transform-02 | model / transform / false / - | model / transform / false / - | 通过 → 通过 |
| web-01 | model / fresh_web / true / - | model / fresh_web / true / - | 通过 → 通过 |
| web-02 | model / url_read / false / - | model / url_read / false / - | 通过 → 通过 |
| web-03 | model / fresh_web / true / - | model / fresh_web / true / - | 通过 → 通过 |
| web-04 | model / fresh_web / true / - | model / fresh_web / true / - | 通过 → 通过 |
| abstract-01 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| abstract-02 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| abstract-03 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| abstract-04 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| abstract-05 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| boundary-01 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| boundary-02 | model / transform / false / - | model / transform / false / - | 通过 → 通过 |
| boundary-03 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| identity-01 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| identity-02 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| identity-03 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| identity-04 | model / clarification_only / false / - | model / direct / false / - | 未中 → 通过 |
| identity-05 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| mcp_alias-01 | model / clarification_only / false / - | model / mcp_explicit / false / - | 未中 → 通过 |
| mcp_alias-02 | model / clarification_only / false / - | model / mcp_explicit / false / - | 未中 → 通过 |
| mcp_alias-03 | model / clarification_only / false / - | model / verified_web / true / - | 未中 → 通过 |
| mcp_alias-04 | model / clarification_only / false / - | model / weather / true / - | 未中 → 通过 |
| verify_verb-01 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| verify_verb-02 | model / direct / false / - | model / direct / false / - | 通过 → 通过 |
| verify_verb-03 | model / verified_web / true / - | model / verified_web / true / - | 通过 → 通过 |
| verify_verb-04 | model / verified_web / true / - | model / verified_web / true / - | 通过 → 通过 |
| verify_verb-05 | model / verified_web / true / - | model / verified_web / true / - | 通过 → 通过 |

## 真实别名与边界

dev 当前目录有 14 个授权别名，其中 3 个有可执行定义与 handler。另取四个自然句，与上表同一轮交错执行：

| 场景 | 基线 | 候选 | 候选能力边界 |
| --- | --- | --- | --- |
| real-available-direct | model / clarification_only / false / - | model / mcp_explicit / false / - | `web_search, url_read, mcp_npSAA24F_VlSUF97F0m0SGMlnCPfpT5WhI-toJ5jIqA` |
| real-missing-direct | model / clarification_only / false / - | model / clarification_only / false / - | `无` |
| real-available-weather | model / clarification_only / false / - | model / weather / true / - | `web_search, url_read, weather_forecast` |
| real-missing-weather | model / clarification_only / false / - | model / weather / true / - | `web_search, url_read, weather_forecast` |


- **实测**：直接指名真实可执行别名时，候选公告该 MCP 工具及联网恢复工具；不在授权目录的别名仍落 `clarification_only`。这是路由决策与工具公告，不是工具执行。
- **实测**：`mcp_alias-03` 候选恢复 `verified_web`，`mcp_alias-04` 恢复 `weather`。但是这两种复合请求的 `external_tool_names` 均没有被指名的 MCP 别名。真实可用别名加天气也一样。包命中不能证明整个复合任务可执行。当前契约固定标准能力包的工具集合，暂不能同时公告 MCP 别名；#137 的实际工具与证据链验收仍需单独定位，不能凭本轮关闭。
- **实测**：提示词首版使 `weather-03`（上海今天空气质量）从基线 `fresh_web` 变为候选 `clarification_only`，交错复核连续 5 组相同。把当前环境测量纳入 `fresh_web` 的语义说明后，另 5 组两臂均为 `fresh_web`；随后重跑完整 47 条，结果见上表。没有改 fixture 或新增文本判据。
- **限制**：`mcp_notion_search` 和 `mcp_calendar_list_events` 是 fixture 提供的合成别名，并非 dev 当前可执行目录。此轮真实别名测试只证明分类与公告边界；尚无真实页面 MCP 工具调用或结果注入证据。单轮 47 条不能估计总体正确率。
