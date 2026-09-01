#!/usr/bin/env bash
set -e

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
management_enabled="$(printf '%s' "${DEPLOY_LITELLM_MODEL_MANAGEMENT_ENABLED}" | tr '[:upper:]' '[:lower:]')"
worker_enabled="$(printf '%s' "${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" | tr '[:upper:]' '[:lower:]')"
release_dir="${HOME}/.local/share/fusion/litellm-model-management-src-${DEPLOY_TARGET_SHA}"
current_link="${HOME}/.local/share/fusion/litellm-model-management-current"
state_dir="${HOME}/.local/state/fusion/litellm-model-management"
acceptance_dir="${HOME}/.local/share/fusion/litellm-acceptance"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 0755 "${release_dir}/scripts" "${unit_dir}"
install -d -m 0700 "${state_dir}" "${acceptance_dir}"
systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-model-management.service; then
  if [ "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ACTIVE}" = "true" ]; then
    systemctl --user start fusion-litellm-model-management.timer
    systemctl --user is-active --quiet fusion-litellm-model-management.timer
  fi
  echo "模型准入 Worker 仍在执行，拒绝切换运行版本；定时器已恢复部署前状态"
  exit 1
fi
for script in \
  audit_litellm_model_catalog.py \
  check_litellm_candidate_preflight.py \
  check_litellm_governance_runtime.py \
  check_litellm_model_management_worker_env.py \
  configure_litellm_model_management_worker_env.py \
  execute_litellm_candidate_admission.py \
  plan_litellm_candidate_admission.py \
  run_litellm_governance_unit.py \
  run_litellm_model_management_worker.py \
  verify_litellm_governance_snapshot.py; do
  install -m 0644 "${GITHUB_WORKSPACE}/backend/scripts/${script}" "${release_dir}/scripts/${script}"
done
install -m 0644 \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-model-management.service" \
  "${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-model-management.timer" \
  "${unit_dir}/"
if [ -e "${current_link}" ] && [ ! -L "${current_link}" ]; then
  echo "模型准入 Worker 当前版本路径不是符号链接，拒绝覆盖"
  exit 1
fi
ln -sfn "${release_dir}" "${current_link}"
systemctl --user daemon-reload
systemd-analyze --user verify \
  "${unit_dir}/fusion-litellm-model-management.service" \
  "${unit_dir}/fusion-litellm-model-management.timer"

if [ "${management_enabled}" != "true" ] || [ "${worker_enabled}" != "true" ]; then
  systemctl --user disable --now fusion-litellm-model-management.timer >/dev/null 2>&1 || true
  echo "模型准入 Worker 未启用；已安装版本化运行文件并保持定时器关闭"
  exit 0
fi
if [ -z "${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_TOKEN}" ]; then
  echo "模型准入 Worker 已启用，但发布密钥未配置"
  exit 1
fi

proxy_env="${HOME}/project/litellm-proxy/.env"
governance_env="${HOME}/.config/fusion/litellm-governance.env"
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

set +u
set -a
source "${HOME}/project/fusion/.env"
set +a
set -u
export LITELLM_VIRTUAL_KEY="${LITELLM_API_KEY:-}"
export LITELLM_MODEL_ADMISSION_WORKER_TOKEN="${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_TOKEN}"
export FUSION_MODEL_MANAGEMENT_BASE_URL="http://127.0.0.1:8002"
export LITELLM_GOVERNANCE_MAX_AGE_SECONDS="${DEPLOY_LITELLM_GOVERNANCE_MAX_AGE_SECONDS}"
expected_token_sha256="$(printf '%s' "${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_TOKEN}" | sha256sum | awk '{print $1}')"
cd "${current_link}"
"${HOME}/.local/share/fusion/litellm-governance-venv/bin/python" \
  -m scripts.configure_litellm_model_management_worker_env \
  --env-file "${governance_env}"
"${HOME}/.local/share/fusion/litellm-governance-venv/bin/python" \
  -m scripts.run_litellm_governance_unit \
  --proxy-env "${proxy_env}" \
  --governance-env "${governance_env}" \
  --registry "${HOME}/.config/fusion/litellm-provider-registry.json" \
  --require-env LITELLM_MASTER_KEY \
  --require-env LITELLM_VIRTUAL_KEY \
  --require-env LITELLM_MODEL_ADMISSION_WORKER_TOKEN \
  --require-env LITELLM_GOVERNANCE_MAX_AGE_SECONDS \
  --require-env FUSION_MODEL_MANAGEMENT_BASE_URL \
  --require-env LITELLM_CANDIDATE_KEY \
  -- \
  "${HOME}/.local/share/fusion/litellm-governance-venv/bin/python" \
    -m scripts.check_litellm_model_management_worker_env \
    --expected-token-sha256 "${expected_token_sha256}" \
    --expected-base-url http://127.0.0.1:8002 \
    --expected-governance-max-age-seconds "${LITELLM_GOVERNANCE_MAX_AGE_SECONDS}"
systemctl --user enable --now fusion-litellm-model-management.timer
systemctl --user is-active --quiet fusion-litellm-model-management.timer
