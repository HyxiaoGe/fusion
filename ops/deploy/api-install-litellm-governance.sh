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
release_dir="${HOME}/.local/share/fusion/litellm-governance-src-${DEPLOY_TARGET_SHA}"
current_link="${HOME}/.local/share/fusion/litellm-governance-current"
config_dir="${HOME}/.config/fusion"
registry_target="${config_dir}/litellm-provider-registry.json"
proxy_env="${HOME}/project/litellm-proxy/.env"
governance_env="${HOME}/.config/fusion/litellm-governance.env"
governance_output_dir="${HOME}/backups/litellm-governance"
acceptance_dir="${HOME}/.local/share/fusion/litellm-acceptance"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 0755 "${release_dir}/scripts" "${release_dir}/ops/litellm" "${unit_dir}"
install -d -m 0700 "${config_dir}" "${governance_output_dir}" "${acceptance_dir}"
restore_timers_on_error() {
  if [ "${ROLLBACK_GOVERNANCE_TIMER_ACTIVE}" = "true" ]; then
    systemctl --user start fusion-litellm-governance.timer >/dev/null 2>&1 || true
  fi
  if [ "${ROLLBACK_COST_TIMER_ACTIVE}" = "true" ]; then
    systemctl --user start fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
  fi
}
trap restore_timers_on_error ERR
systemctl --user stop fusion-litellm-governance.timer >/dev/null 2>&1 || true
systemctl --user stop fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-governance.service \
  || systemctl --user is-active --quiet fusion-litellm-cost-sync.service; then
  echo "LiteLLM 治理或成本同步周期仍在执行，拒绝切换运行版本"
  exit 1
fi
for script in \
  check_litellm_candidate_preflight.py \
  check_litellm_governance_runtime.py \
  discover_litellm_model_candidates.py \
  enrich_litellm_model_candidates.py \
  ensure_litellm_cost_map_sync.py \
  fetch_litellm_cost_map.py \
  check_litellm_cost_map_sync_status.py \
  orchestrate_litellm_model_candidates.py \
  plan_litellm_candidate_admission.py \
  run_litellm_governance_cycle.py \
  run_litellm_governance_unit.py; do
  install -m 0644 "${GITHUB_WORKSPACE}/backend/scripts/${script}" "${release_dir}/scripts/${script}"
done
install -m 0644 \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/candidate-overrides.json" \
  "${release_dir}/ops/litellm/candidate-overrides.json"
if [ -e "${current_link}" ] && [ ! -L "${current_link}" ]; then
  echo "LiteLLM 治理当前版本路径不是符号链接，拒绝覆盖"
  exit 1
fi
if [ -e "${registry_target}" ] && [ -L "${registry_target}" ]; then
  echo "LiteLLM provider registry 不能是符号链接"
  exit 1
fi
if [ ! -e "${registry_target}" ]; then
  registry_temp="$(mktemp "${config_dir}/.litellm-provider-registry.XXXXXX")"
  trap 'rm -f "${registry_temp}"' EXIT
  install -m 0600 \
    "${GITHUB_WORKSPACE}/backend/ops/litellm/provider-registry.example.json" \
    "${registry_temp}"
  mv -f "${registry_temp}" "${registry_target}"
  trap - EXIT
fi
python3 - "${registry_target}" <<'PY'
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
file_stat = path.lstat()
if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
    raise SystemExit("LiteLLM provider registry 必须是当前用户拥有的普通文件")
if stat.S_IMODE(file_stat.st_mode) & 0o077:
    raise SystemExit("LiteLLM provider registry 权限过宽")
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
    raise SystemExit("LiteLLM provider registry 结构无效")
PY
python3 - "${proxy_env}" "${governance_env}" <<'PY'
import os
import pathlib
import stat
import sys

for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path)
    file_stat = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise SystemExit(f"治理密钥文件不是普通文件: {path}")
    if file_stat.st_uid != os.getuid():
        raise SystemExit(f"治理密钥文件 owner 不匹配: {path}")
    os.chmod(path, 0o600, follow_symlinks=False)
PY
install -m 0644 \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-governance.service" \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-governance.timer" \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-cost-sync.service" \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-cost-sync.timer" \
  "${unit_dir}/"
ln -sfn "${release_dir}" "${current_link}"
systemctl --user daemon-reload
systemd-analyze --user verify \
  "${unit_dir}/fusion-litellm-governance.service" \
  "${unit_dir}/fusion-litellm-governance.timer" \
  "${unit_dir}/fusion-litellm-cost-sync.service" \
  "${unit_dir}/fusion-litellm-cost-sync.timer"
governance_python="${HOME}/.local/share/fusion/litellm-governance-venv/bin/python"
systemctl --user reset-failed fusion-litellm-governance.service >/dev/null 2>&1 || true
(
  cd "${current_link}"
  "${governance_python}" -m scripts.run_litellm_governance_unit \
    --proxy-env "${proxy_env}" \
    --governance-env "${governance_env}" \
    --registry "${registry_target}" \
    --require-env LITELLM_MASTER_KEY \
    --require-env LITELLM_CANDIDATE_KEY \
    -- /usr/bin/true
)
systemctl --user enable --now fusion-litellm-governance.timer
systemctl --user is-active --quiet fusion-litellm-governance.timer
systemctl --user reset-failed fusion-litellm-cost-sync.service >/dev/null 2>&1 || true
systemctl --user start fusion-litellm-cost-sync.service
[ "$(systemctl --user show fusion-litellm-cost-sync.service -p Result --value)" = "success" ]
[ "$(systemctl --user show fusion-litellm-cost-sync.service -p ExecMainStatus --value)" = "0" ]
systemctl --user enable --now fusion-litellm-cost-sync.timer
systemctl --user is-active --quiet fusion-litellm-cost-sync.timer
trap - ERR
