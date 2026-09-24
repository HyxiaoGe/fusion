# #141 `fresh_web` 类别级语义：同容器模型配对（2026-09-24）

## 结论范围

**实测**：第三版类别表述在三条指定未列举量测问句、三条另行保留的量测问句及对照句上，连续五组均取得各自预期包。原始 47 条盲测连续三组，基线与候选的逐条 `layer / package / include_current_date / error` 均相同，候选没有新未中。下文给出逐条摘要；每次调用的原始结构化响应全文保存在 JSONL，不把单次或这组样本外推为总体准确率。

**代码阅读**：只改 `backend/app/ai/prompts/runtime_prompts.toml` 中的 `fresh_web` 类别句。未改契约、能力解析、持久化、Trajectory、产品答案校验器或 #132 删除的判据；新测试数据独立于原 47 条 fixture。

最终类别句：`fresh_web: latest or current external facts, news, public releases, and measured values that vary over time; use this for live observations unless a specialized tool below explicitly covers the requested measured value; [web_search].`

## 环境与来源

- dev 正在运行的 `fusion-api` 镜像：`sha256:a8527a4f2f9ff19ca43993bbdaf2dcb2a610f11def1fedab4625e9a799dfe97e`；仓库 dev 发布 run `36003844009` 对应 master `7c76a43be67ff11471d50f0d83c2e376ccf998ae` 且成功。取数结束后再次读取镜像 ID 与基线提示词哈希，均未变化。
- **文件核对**：`/app/app/ai/prompts/runtime_prompts.toml` 与该 master 文件仅有 CRLF/LF 差异；统一 LF 后 SHA-256 为 `ad9e1986c5d28a243ebef60a4d588ba6e0fcf213f101100f2d82c213e0ba6ce1`。候选 `/tmp/issue141-candidate/app/ai/prompts/runtime_prompts.toml` 与本地候选字节相同，SHA-256 为 `0e0fbae58a5898a6280a85bf7dec9e4dd18071ba9f962973dc0639c0fcfed794`。两臂分类器源码字节 SHA-256 均为 `d3a5d8290a5fa2d2f776fb438b4bccd4ea47ccf5a3c2a7dc0f36b47f0630cef6`。
- **实际导入路径**：每条 JSONL 都核验基线为 `/app/app/services/stream/run_capability_model_classifier.py`、候选为 `/tmp/issue141-candidate/app/services/stream/run_capability_model_classifier.py`。运行中的 `/app` 未改。
- **取数方式**：同一 dev 容器中两个独立 Python worker 交错调用真实 LiteLLM 分类模型；包装 `litellm.completion` 仅记录返回的 `message.content` 原字符串，交给原分类器解析与路由。每条均保存 `layer / package / include_current_date / error`、`response_id` 和实际公告工具。worker 的 stderr 仅有分类 INFO，driver stderr 为空。
- **缓存状态**：响应没有提供明确的 `cache_hit` 标记；是否命中缓存取不到。没有用短延迟推定缓存状态。
- **本地测试环境**：Ruff、TOML/JSON 解析和原始 JSONL 完整性检查通过。本机目标 pytest 在收集阶段因缺少 `mcp` 包而未运行测试；需由 PR 后端 CI 补足，不能把本机收集错误写作测试通过。

## 三版类别措辞与定向复核

三版均未在提示词中列举紫外线、花粉、水质。每版均使用同一 dev 基线，六条问句每条连续五组交错。前两版失败，因而没有拿它们跑完整 47 条：

| 候选 | SHA-256 前缀 | 指定三条中的稳定差异 | 处理 |
| --- | --- | --- | --- |
| 第一版：时变量测且无专用包直接提供 | `dbc33153` | 水质：基线 `fresh_web` 5/5，候选 `clarification_only` 5/5 | 作废 |
| 第二版：时变量测需要最新外部证据 | `5faa70ed` | 紫外线：基线 `fresh_web` 5/5，候选 `weather` 5/5 | 作废 |
| 第三版：时变观测值，只有专用工具明确覆盖该值时才转给专用包 | `0e0fbae5` | 指定三条基线与候选各 5/5 为 `fresh_web` | 进入完整盲测 |

这些差异是**模型返回与路由实测**；“第一版的专用包限定造成水质澄清”“第二版未讲清天气工具边界”只是可能解释，不能从结构化响应证明其内部原因。第一版、第二版、第三版的原始逐次结果分别见 [第一版](evidence/2026-09-24-issue141/targeted-first.jsonl)、[第二版](evidence/2026-09-24-issue141/targeted-second.jsonl)、[第三版](evidence/2026-09-24-issue141/targeted-final.jsonl)。

第三版定向结果，每格为 `layer / package / include_current_date / error`；每条原始响应都在 JSONL 的 `raw_structured_response.content`。三条指定量测请求的原始内容均为 `{"package_id": "fresh_web", "explicit_tool_names": ["web_search"]}`，且每次都由原分类器解析为 `fresh_web`；完整原值仍以 JSONL 为准。

| ID | 基线 | 候选 | 连续复核 |
| --- | --- | --- | --- |
| measurement-uv | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| measurement-pollen | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| measurement-water | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| weather-01 | model / weather / true / - | model / weather / true / - | 5 组 |
| weather-03 | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| direct-03 | model / direct / false / - | model / direct / false / - | 5 组 |

另有三条**第三版措辞确定后才加入**的量测问句：环境噪声、近岸潮位、地磁 K 指数；它们均未出现在提示词中。原始逐次结果见 [额外保留组](evidence/2026-09-24-issue141/heldout-final.jsonl)。

| ID | 基线 | 候选 | 连续复核 |
| --- | --- | --- | --- |
| heldout-noise | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| heldout-tide | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| heldout-geomagnetic | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| weather-01 | model / weather / true / - | model / weather / true / - | 5 组 |
| weather-03 | model / fresh_web / true / - | model / fresh_web / true / - | 5 组 |
| direct-03 | model / direct / false / - | model / direct / false / - | 5 组 |

## 原 47 条盲测

本节是同一环境同一轮的三次交错复核；不与历史 47 条评分拼接，也不把 47 条视为总体准确率。每格仍为 `layer / package / include_current_date / error`。完整 141 条配对及每次原始结构化响应见 [盲测原始 JSONL](evidence/2026-09-24-issue141/blind-final.jsonl)。每个 ID 的三组内部及两臂结果本轮相同：

| ID | 基线 | 候选 | 连续复核 |
| --- | --- | --- | --- |
| travel-01 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | 3 组 |
| travel-02 | model / mobility_route / true / - | model / mobility_route / true / - | 3 组 |
| travel-03 | model / flight / true / - | model / flight / true / - | 3 组 |
| travel-04 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | 3 组 |
| travel-05 | model / mobility_route / true / - | model / mobility_route / true / - | 3 组 |
| travel-06 | model / mobility_route / true / - | model / mobility_route / true / - | 3 组 |
| travel-07 | model / clarification_only / false / - | model / clarification_only / false / - | 3 组 |
| travel-08 | model / clarification_only / false / - | model / clarification_only / false / - | 3 组 |
| travel-09 | model / mobility_intercity / true / - | model / mobility_intercity / true / - | 3 组 |
| travel-10 | model / train / true / - | model / train / true / - | 3 组 |
| weather-01 | model / weather / true / - | model / weather / true / - | 3 组 |
| weather-02 | model / weather / true / - | model / weather / true / - | 3 组 |
| weather-03 | model / fresh_web / true / - | model / fresh_web / true / - | 3 组 |
| place-01 | model / place_discovery / false / - | model / place_discovery / false / - | 3 组 |
| place-02 | model / place_discovery / false / - | model / place_discovery / false / - | 3 组 |
| direct-01 | model / direct / false / - | model / direct / false / - | 3 组 |
| direct-02 | model / direct / false / - | model / direct / false / - | 3 组 |
| direct-03 | model / direct / false / - | model / direct / false / - | 3 组 |
| direct-04 | model / direct / false / - | model / direct / false / - | 3 组 |
| transform-01 | model / transform / false / - | model / transform / false / - | 3 组 |
| transform-02 | model / transform / false / - | model / transform / false / - | 3 组 |
| web-01 | model / fresh_web / true / - | model / fresh_web / true / - | 3 组 |
| web-02 | model / url_read / false / - | model / url_read / false / - | 3 组 |
| web-03 | model / fresh_web / true / - | model / fresh_web / true / - | 3 组 |
| web-04 | model / fresh_web / true / - | model / fresh_web / true / - | 3 组 |
| abstract-01 | model / direct / false / - | model / direct / false / - | 3 组 |
| abstract-02 | model / direct / false / - | model / direct / false / - | 3 组 |
| abstract-03 | model / direct / false / - | model / direct / false / - | 3 组 |
| abstract-04 | model / direct / false / - | model / direct / false / - | 3 组 |
| abstract-05 | model / direct / false / - | model / direct / false / - | 3 组 |
| boundary-01 | model / direct / false / - | model / direct / false / - | 3 组 |
| boundary-02 | model / transform / false / - | model / transform / false / - | 3 组 |
| boundary-03 | model / direct / false / - | model / direct / false / - | 3 组 |
| identity-01 | model / direct / false / - | model / direct / false / - | 3 组 |
| identity-02 | model / direct / false / - | model / direct / false / - | 3 组 |
| identity-03 | model / direct / false / - | model / direct / false / - | 3 组 |
| identity-04 | model / direct / false / - | model / direct / false / - | 3 组 |
| identity-05 | model / direct / false / - | model / direct / false / - | 3 组 |
| mcp_alias-01 | model / mcp_explicit / false / - | model / mcp_explicit / false / - | 3 组 |
| mcp_alias-02 | model / mcp_explicit / false / - | model / mcp_explicit / false / - | 3 组 |
| mcp_alias-03 | model / verified_web / true / - | model / verified_web / true / - | 3 组 |
| mcp_alias-04 | model / weather / true / - | model / weather / true / - | 3 组 |
| verify_verb-01 | model / direct / false / - | model / direct / false / - | 3 组 |
| verify_verb-02 | model / direct / false / - | model / direct / false / - | 3 组 |
| verify_verb-03 | model / verified_web / true / - | model / verified_web / true / - | 3 组 |
| verify_verb-04 | model / verified_web / true / - | model / verified_web / true / - | 3 组 |
| verify_verb-05 | model / verified_web / true / - | model / verified_web / true / - | 3 组 |

## 范围与静态核对

- `rg -ni '紫外线|花粉|水质|ultraviolet|pollen|water quality' backend/app/ai/prompts/runtime_prompts.toml` 无命中；最终类别句不含具名量测示例。
- `backend/app` 的 `re.compile(` 数量在本次 diff 前后都是 **214**。#140 所在 master 曾为 213；其后 #139 在 `research_evidence.py` 新增一条 `_REQUEST_URL_RE`，本 PR 没碰它。故按当前 master 给出 `214 → 214`，不能把旧 213 写作当前数。
- 未做 #107、#137 取数；没有改变 `product_answer_validator.py` 或 #24 的契约与持久化边界。
