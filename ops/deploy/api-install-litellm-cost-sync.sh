#!/usr/bin/env bash
set -eo pipefail

if [ "${OPS_DEPLOY_DRY_RUN:-false}" = "true" ]; then
  if [ "${GITHUB_ACTIONS:-false}" = "true" ]; then
    printf "GitHub Actions 不允许部署脚本 dry-run\n" >&2
    exit 1
  fi
  printf "DRY-RUN %s\n" "${BASH_SOURCE[0]##*/}"
  exit 0
fi

# OPS_DEPLOY_BODY_BEGIN
set -euo pipefail
# cost-sync 沿用 litellm-governance-* 版本目录与 venv，路径已写进 systemd unit。
release_dir="${HOME}/.local/share/fusion/litellm-governance-src-${DEPLOY_TARGET_SHA}"
current_link="${HOME}/.local/share/fusion/litellm-governance-current"
proxy_env="${HOME}/project/litellm-proxy/.env"
governance_env="${HOME}/.config/fusion/litellm-governance.env"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 0755 "${release_dir}/scripts" "${unit_dir}"
restore_timer_on_error() {
  if [ "${ROLLBACK_COST_TIMER_ACTIVE}" = "true" ]; then
    systemctl --user start fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
  fi
}
trap restore_timer_on_error ERR
# 模型上下线已改用 scripts/model_onboard.py：停用并移除旧的治理发现与准入 Worker 单元。
for retired_unit in fusion-litellm-governance fusion-litellm-model-management; do
  systemctl --user disable --now "${retired_unit}.timer" >/dev/null 2>&1 || true
  if systemctl --user is-active --quiet "${retired_unit}.service"; then
    echo "${retired_unit} 仍在执行，拒绝移除"
    exit 1
  fi
  rm -f "${unit_dir}/${retired_unit}.service" "${unit_dir}/${retired_unit}.timer"
done
retired_worker_link="${HOME}/.local/share/fusion/litellm-model-management-current"
if [ -L "${retired_worker_link}" ]; then
  unlink "${retired_worker_link}"
fi
systemctl --user stop fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-cost-sync.service; then
  echo "LiteLLM 成本同步周期仍在执行，拒绝切换运行版本"
  exit 1
fi
for script in \
  check_litellm_governance_runtime.py \
  ensure_litellm_cost_map_sync.py \
  check_litellm_cost_map_sync_status.py \
  run_litellm_governance_unit.py; do
  install -m 0644 "${GITHUB_WORKSPACE}/backend/scripts/${script}" "${release_dir}/scripts/${script}"
done
if [ -e "${current_link}" ] && [ ! -L "${current_link}" ]; then
  echo "LiteLLM 成本同步当前版本路径不是符号链接，拒绝覆盖"
  exit 1
fi
python3 - "${proxy_env}" "${governance_env}" <<'PY'
import os
import pathlib
import stat
import sys

for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path)
    file_stat = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise SystemExit(f"成本同步密钥文件不是普通文件: {path}")
    if file_stat.st_uid != os.getuid():
        raise SystemExit(f"成本同步密钥文件 owner 不匹配: {path}")
    os.chmod(path, 0o600, follow_symlinks=False)
PY
install -m 0644 \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-cost-sync.service" \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-cost-sync.timer" \
  "${unit_dir}/"
ln -sfn "${release_dir}" "${current_link}"
systemctl --user daemon-reload
systemd-analyze --user verify \
  "${unit_dir}/fusion-litellm-cost-sync.service" \
  "${unit_dir}/fusion-litellm-cost-sync.timer"
systemctl --user reset-failed fusion-litellm-cost-sync.service >/dev/null 2>&1 || true
systemctl --user start fusion-litellm-cost-sync.service
[ "$(systemctl --user show fusion-litellm-cost-sync.service -p Result --value)" = "success" ]
[ "$(systemctl --user show fusion-litellm-cost-sync.service -p ExecMainStatus --value)" = "0" ]
systemctl --user enable --now fusion-litellm-cost-sync.timer
systemctl --user is-active --quiet fusion-litellm-cost-sync.timer
trap - ERR
