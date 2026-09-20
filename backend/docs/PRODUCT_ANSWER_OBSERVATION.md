# 产品回答观测取数

`product_answer_observations` 是独立数据库表，记录产品回答延迟提交路径的决策。正常写入等待独立事务完成；没有 TTL、定时清理或会话外键，不随会话删除或 `app.log` 轮转删除。备份与恢复沿用应用数据库策略。需要清理时另行明确授权并先导出观测期数据；不承诺数据库故障或手工删除后仍零丢失。

迁移只创建空表，不导入或重建 #71 的旧 17 条日志。**本 PR 不部署，也不启动真实观测期。** 部署后需先核对新代码、迁移和实际落库，再由实际新记录确定新观测期起点；`retained_range.first` 是所查范围内首条留存记录，不是流量开始时间或完整留存证明。

## 字段与分母

- `observation_path=validated`：实际调用 validator；`validated=true`，`is_valid` 对应其结果。`reason_code` / `reason_category` 分布以及类别内部比例都只用这部分作分母。
- `weather_activity` / `mixed_travel` / `single_travel_comparison`：三个确定性短路；`validated=false`、`reason_code=not_validated`、`is_valid=null`。这些回答没有被 validator 校验，不能把它们填成 `ok` 抬高校验通过率。
- `no_product_result`：无真实产品结果的兜底，沿用 #101 的无结果早退路径；同样没有经过 validator。
- `product_tool_attempted`：当次状态是否尝试过产品工具。`product_result_types` 保留固定块类型，不含块内容。未知类型或 reason code 收敛到 `other`，不记录任何原文。

`all_observed_decisions` 为**已成功留存的产品回答延迟提交决策**，包括校验子集、三个短路和无结果兜底；它不是所有 Run 或全站最终回答数。此前的终止、知识库、联网恢复与工具澄清路径不在此范围，重复决策也不能当作去重的用户数。接口明确返回 `coverage.scope` 与未知的 `unobserved_count/complete`。

`invalid_among_validated` 的分母是 `validated_decisions`；`invalid_among_all_observed` 的分母是上述完整留存群体。后者表示这批决策中实际出现了多少校验拒绝，**不代表其余都通过校验**。`repair_available` 只表示纯改写函数产出非 None，是可能受改写影响的上界，不能当作误伤率；没有人工标注也不能推断正确拦截和误伤。零样本的比例为 `null`，脚本不判断样本是否足够。

## 取数

沿用现有超级管理员/会话审计员鉴权，访问会写管理员访问审计。无需 SSH，也无需扫描应用日志。使用当前有效的管理员访问令牌设置本地环境变量 `FUSION_API_TOKEN`，然后运行：

```sh
python scripts/product_answer_observation_report.py \
  --base-url 'https://你的-Fusion-API' \
  --from '2026-09-21T00:00:00+08:00' \
  --to '2026-09-22T00:00:00+08:00'
```

上面时间仅为命令示例，不代表观测期已开始。脚本原样输出 API JSON，不打印令牌。底层接口为 `GET /api/admin/audit/product-answer-observations?from=...&to=...`，起点包含、终点不包含；建议显式传 `+08:00`。无时区输入按 `Asia/Shanghai` 解释，数据库按现有 UTC 约定存储和查询。SQL 在数据库端按固定维度聚合，接口不会返回模型、用户或工具正文。

## 写入故障与完整性

`PRODUCT_ANSWER_OBSERVATION_STORE_FAILED error_type=...` 是独立数据库写入失败诊断，不包含异常正文或数据库凭据。该故障不会改变用户答案，失败记录不在数据库聚合分母内。应用日志仍保留同份脱敏观测，便于排障，但不是持久取数依据。

写入使用独立的两个工作线程与两个准入名额，不建立无界队列，也不占用其他模型/上下文工作线程。答案最多等待 1 秒；`PRODUCT_ANSWER_OBSERVATION_STORE_PENDING error_type=wait_timeout` 表示已开始的事务还可能晚到提交，不能据此断言已丢失。超时不会取消事务或释放准入位；只有 worker 真正结束才释放，晚到结果不会重复写入或重新发送用户内容。准入满使用固定 `capacity_exhausted`，提交线程失败使用 `submit_failed`。这两个失败不会排队补写，不能把它们掩盖为正常留存。

API 对完整性返回未知，不会因为数据库中有记录就宣称未漏写。确认故障期间的数据完整性需要额外运行证据；不能从成功记录反推缺失数量，也不能把旧日志样本补成新观测期分母。
