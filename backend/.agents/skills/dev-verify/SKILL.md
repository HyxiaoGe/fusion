---
name: dev-verify
description: 在已授权发布完成后，核验 Fusion dev 的单仓 CI、per-app 发布台账与运行容器 digest/image ID。
allowed-tools: Bash
---

# 验证 Dev 服务器发布状态

push、PR 检查、合并和 dev 发布是不同状态。仅 push 不能证明已发布；本 skill 只在目标发布已获明确授权并实际触发后使用，执行只读核验，不修改 dev 状态。

## 1. 核对单仓 Actions

```bash
gh run list --repo HyxiaoGe/fusion --workflow deploy-dev.yml --limit 5
gh run view {run_id} --repo HyxiaoGe/fusion
```

确认目标 run 对应预期 master SHA，并分别记录 API/UI job 的实际结论；一侧 skipped 不等于另一侧已发布。

## 2. 核对容器与健康

```bash
ssh dev "docker ps --filter name=fusion --format '{{.Names}}: {{.Status}}'"
ssh dev "curl --fail-with-body --silent --show-error http://localhost:8002/health"
```

## 3. 核对运行身份与 per-app 台账

运行容器的 repository digest + image ID 是权威身份，`<sha>` tag 只作审计别名；宿主机 checkout 外的 per-app 发布台账是可恢复投影，不得反向覆盖运行证据。

```bash
ssh dev "docker inspect fusion-api --format '{{.Image}}'"
ssh dev "docker image inspect {api_image_id} --format '{{json .RepoDigests}}'"
ssh dev "docker inspect fusion-ui --format '{{.Image}}'"
ssh dev "docker image inspect {ui_image_id} --format '{{json .RepoDigests}}'"
ssh dev "python3 -m json.tool ~/.local/share/fusion/api/release-ledger.json"
ssh dev "python3 -m json.tool ~/.local/share/fusion/ui/release-ledger.json"
```

逐应用比对台账 `current_sha`、repository digest、image ID 与实际容器。任一 digest 无法解析、台账缺失或身份不一致都必须如实报告，不能仅凭容器 `running`、健康 200 或 tag 宣称发布完成。

## 4. 应用级只读状态

```bash
ssh dev "docker logs fusion-api 2>&1 | grep 'Redis' | tail -5"
ssh dev "docker exec middleware-redis redis-cli keys 'stream:*'"
```

若要创建会话、发送消息、消耗模型额度或写 dev 状态，必须另行取得显式验收授权并使用 `dev-test-api`；本 skill 不包含这些动作。
