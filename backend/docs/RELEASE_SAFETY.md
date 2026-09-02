# Backend 发布安全边界

本文是 Fusion Backend 发布与回滚的工程事实源。公开项目介绍不承载机器契约；相关自动化测试应读取本文和实际工作流。

## 镜像身份

- 构建产物可以带提交 SHA 标签，用于审计、查找和兼容输入。
- 提交 SHA 标签只用于审计与兼容输入，不得作为部署权威身份。
- 发布流程必须从 registry 解析 repository digest，并以 digest 作为候选部署身份。
- 部署后同时核对容器的镜像引用和实际 image ID，不能只检查可变标签。

## 数据库迁移

- 数据库 schema 只按 expand/contract 演进：先发布向后兼容的扩展，再在所有可回滚版本不再依赖旧结构后独立收缩。
- 镜像回滚绝不执行 `alembic downgrade`。
- 执行手动回滚前，必须确认目标应用版本与当前 schema 兼容。

## 回滚

- 候选部署开始前必须记录当前 API 与 FlyAI Adapter 的镜像引用、image ID 和对应提交。
- 候选镜像身份、健康检查或 deployment smoke 任一失败，都应恢复完整的旧镜像组合，并再次验证身份与健康状态。
- 自动回滚成功不能掩盖原发布失败；回滚失败也必须作为独立失败暴露。
- 手动回滚目标必须能从发布账本唯一解析，不能从模糊前缀或当前 registry 标签猜测。

## 发布顺序

- Backend 数据库迁移与 API 健康检查成功后，才允许继续 Frontend 发布。
- Frontend 单独变更可以只发布 UI，但不得改变已部署 API 的身份。

相关实现位于根工作流 `/.github/workflows/_deploy-api.yml`、`/.github/workflows/deploy-dev.yml` 和 `ops/deploy/`。
