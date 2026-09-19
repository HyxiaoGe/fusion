---
name: dev-verify
description: 只读核验 Fusion dev 的 Actions、逐应用发布台账与实际运行镜像身份。
---

# Dev 运行版本核验

用于已发生发布的核验或用户要求的只读状态调查，无需额外取得部署权限。它不触发部署，不重启服务，也不代表业务验收已通过。

```bash
gh run list --repo HyxiaoGe/fusion --workflow deploy-dev.yml --limit 5
ssh dev 'docker ps --filter name=fusion --format "{{.Names}}: {{.Status}}"'
ssh dev 'docker inspect fusion-api --format "{{.Image}}"'
ssh dev 'docker inspect fusion-ui --format "{{.Image}}"'
```

按目标 SHA 查看相应 run 的 API/UI job；使用取得的 image ID 查询 `docker image inspect` 的 RepoDigests。分别与 `~/.local/share/fusion/api/release-ledger.json`、`~/.local/share/fusion/ui/release-ledger.json` 的 current_sha、digest 和 image ID 核对，只保留所需字段。

运行容器的 digest/image ID 是身份依据，SHA tag 是审计别名，台账是投影；不从宿主机 checkout 或容器 running 推断版本。某应用 skipped 时记录它实际运行的版本，不宣称两侧都更新。

健康 URL 和端口从当前部署脚本确认，再核对目标应用与必要依赖。台账缺失、digest 无法解析、运行身份不一致或检查失败分别报告。创建会话、模型调用和产品路径由验收授权及 `dev-test-api` 处理。
