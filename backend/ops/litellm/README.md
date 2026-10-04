# LiteLLM 运维门禁

本目录保存 LiteLLM 的可版本化升级约束，不包含密钥、数据库地址或厂商配置。
除明确写出绝对路径的备份集成示例外，下面所有 `python -m scripts...` 命令都
从 `fusion-api` 仓库根目录执行。

## Compose 合并检查

在部署目录执行：

```bash
docker compose \
  -f docker-compose.yml \
  -f /path/to/fusion-api/ops/litellm/docker-compose.governance.yml \
  config --quiet
```

治理覆盖文件固定经过隔离验证的版本和 digest，增加 readiness healthcheck，并关闭 Watchtower 自动滚动。

## 升级前检查

```bash
python -m scripts.check_litellm_upgrade_readiness \
  --base-url http://127.0.0.1:4000 \
  --primary-backup /path/to/latest-postgres-dump.sql.gz \
  --secondary-backup-marker /path/to/latest-restic-success.marker
```

脚本只执行 GET 和本地文件检查。退出码为 `0` 才表示基础门禁通过；成本表未调度只产生 warning，因为首次升级前允许尚未启用调度。

`--secondary-backup-marker` 必须是 JSON，至少包含 `status=success`、完整 `snapshot_id`、带时区的 `completed_at` 和 `tag=daily`；门禁按快照完成时间而不是 marker 文件 mtime 判断新鲜度，因此复制或 `touch` 旧 marker 不能通过。主备份为 `.gz` 时会完整读取并校验 gzip。

### 生成 restic 成功 marker

`write_restic_success_marker.py` 只消费 `restic snapshots --latest 1 --json` 的结果，不执行备份、`forget` 或 `prune`，也不读取 restic 仓库密码。只有最新快照包含完整 `id`、时间带时区且未超过最大年龄时，才会原子替换 marker；校验或写入失败会保留旧 marker。

应当只在 `restic backup` 成功后调用 helper，例如在现有备份脚本中接入：

```bash
RESTIC_SUCCESS_MARKER="$HOME/backups/restic-success.json"

if "$RESTIC" backup "${PATHS[@]}" --tag daily; then
    if "$RESTIC" snapshots --tag daily --latest 1 --json \
        | python /path/to/fusion-api/scripts/write_restic_success_marker.py \
            --snapshots-json - \
            --output "$RESTIC_SUCCESS_MARKER" \
            --max-age-seconds 129600
    then
        log "restic 成功 marker 已更新"
    else
        log "WARN restic 快照 marker 生成失败"
        FAIL=1
    fi
else
    FAIL=1
fi
```

不要在 `finally`、失败分支或独立定时任务中无条件生成 marker。升级门禁使用同一路径：

```bash
python -m scripts.check_litellm_upgrade_readiness \
  --base-url http://127.0.0.1:4000 \
  --primary-backup /path/to/latest-postgres-dump.sql.gz \
  --secondary-backup-marker "$HOME/backups/restic-success.json"
```

也可以先保存只读快照 JSON，再从文件生成 marker：

```bash
restic snapshots --tag daily --latest 1 --json > /tmp/restic-latest.json
python -m scripts.write_restic_success_marker \
  --snapshots-json /tmp/restic-latest.json \
  --output "$HOME/backups/restic-success.json"
```

## 必要隔离证据

- 当日 PostgreSQL 备份完整恢复；
- `v1.93.0` 在数据库副本上完成 migration；
- readiness 返回 DB connected；
- DB model 别名与升级前一致；
- virtual key 数量和 allowlist 不发生意外变化；
- 成本表手动刷新和 6 小时调度成功；
- Fusion 全模型验收按 `docs/MODEL_ACCEPTANCE_RUNBOOK.md` 执行。

缺少任一证据时，不得把覆盖文件应用到运行中的 dev LiteLLM。

## 模型上下线（model_onboard）

新模型上线、下线统一用 `scripts/model_onboard.py`，在 dev 的 fusion-api 容器里执行。master key 只在远端 shell 里从
`~/project/litellm-proxy/.env` 读取，不进入命令文本：

```bash
cd ~/project/litellm-proxy && set -a && . ./.env && set +a
docker exec -i -e LITELLM_MASTER_KEY fusion-api python -m scripts.model_onboard <command>
```

| 命令 | 作用 |
| --- | --- |
| `list` | 列出代理里登记的模型、上游与是否已发布到 Fusion 虚拟 key |
| `add <alias> --upstream ... --api-base ... --api-key-env ... --input-price ... --output-price ... --context-window ... --provider-key ... --provider-display ... [--capabilities ...]` | 在代理登记模型并跑真实预检（会产生少量调用费用）；预检任一项失败自动删除，模型此时对用户不可见 |
| `publish <alias>` | 把模型加入 Fusion 虚拟 key 的 allowlist、回读确认并刷新 Fusion 目录缓存 |
| `retire <alias>` | 代码里仍引用该 alias 时拒绝；否则移出 allowlist、删除代理模型，输出 `backup` 供手工恢复 |

价格单位是 USD / 1M tokens；`--api-key-env` 只写代理进程里的环境变量名（如 `QWEN_API_KEY`），密钥本身不出现在命令里。
上线后的可见性（新对话是否可选）在管理后台「模型管理」中切换。

## 运行时成本表同步

LiteLLM Proxy 的成本表刷新调度保存在进程内存里，重启后会丢失。`fusion-litellm-cost-sync.timer`
每 15 分钟（Asia/Shanghai）幂等检查一次：

- 已按 6 小时健康调度时只 GET，不产生写入；
- 未调度或周期错误时才调用一次 schedule API，再 GET 复核；
- 新建调度后若 LiteLLM 尚未给出 `last_run` / `next_run`，只执行一次 `reload/model_cost_map` 完成冷启动，再次 GET 复核；
- stale、fallback 或异常不会通过反复重排掩盖，service 非零退出并保留 journal 证据。

`master` 发布流水线（`ops/deploy/api-install-litellm-cost-sync.sh`）把脚本复制到带提交 SHA 的
`~/.local/share/fusion/litellm-governance-src-<sha>`，原子切换 `litellm-governance-current` 并安装 unit。
目录与 venv 名沿用历史的 `litellm-governance` 前缀，unit 中的路径与之绑定。首次安装或灾备恢复时准备：

```bash
python3.11 -m venv "$HOME/.local/share/fusion/litellm-governance-venv"
"$HOME/.local/share/fusion/litellm-governance-venv/bin/python" \
  -m pip install -r ops/litellm/requirements-governance.txt
install -m 0600 ops/litellm/litellm-governance.env.example \
  "$HOME/.config/fusion/litellm-governance.env"
chmod 0600 "$HOME/project/litellm-proxy/.env"
```

service 不使用 systemd `EnvironmentFile=`，改由 `run_litellm_governance_unit.py` 以 `O_NOFOLLOW` + `fstat`
检查两份 env 文件（归当前用户、普通文件、权限不宽于 `0600`），只向子进程注入 `LITELLM_MASTER_KEY`
与白名单变量。可以先用默认 dry-run 手工查看计划：

```bash
python -m scripts.ensure_litellm_cost_map_sync
```
