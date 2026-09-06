# P3a 历史同步器夹具

`legacy_v2_contract.json` 从独立桥接提交 `9fc318b693bb7a55fc70577708e5e32ed19f815d` 的真实 validator 和独立 published 夹具生成，保存完整旧引擎原始材料与 v2 payload。SHA-256 为 `442674b68077a82e1d6d4b5a2d6c290e92bf8842eec3106d05f620376bfbd090`；最终运行时必须拒绝重新消费该旧契约，测试不得把旧 payload 重标为 Jinja。

`p3a_sync.py` 逐字复制自 `90962a5c05368d6fbe038c95de6de5bc59da310a:backend/app/services/prompthub_sync_service.py`，用于验证回滚旧 v2 消费者后数据库 hold 仍不可绕过。此夹具不得随当前实现改写。

原始文件 SHA-256：`1fc5150883d4a5f85f022c9278bb779c9a2d37df1567047728c3f6f8e240fb3f`。
