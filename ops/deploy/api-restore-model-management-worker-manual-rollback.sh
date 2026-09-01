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
case "${management_enabled}:${worker_enabled}" in
  true:true|true:false|false:false) ;;
  *) echo "模型管理开关必须为 true 或 false，且 Worker 不能单独启用"; exit 1 ;;
esac
current_link="${HOME}/.local/share/fusion/litellm-model-management-current"
target_release="${HOME}/.local/share/fusion/litellm-model-management-src-${DEPLOY_TARGET_SHA}"
unit_dir="${HOME}/.config/systemd/user"
target_service_unit="${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-model-management.service"
target_timer_unit="${GITHUB_WORKSPACE}/backend/ops/litellm/fusion-litellm-model-management.timer"
systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-model-management.service; then
  echo "模型准入 Worker 仍在执行，拒绝切换回滚版本；定时器已安全暂停"
  exit 1
fi
if [ -e "${current_link}" ] && [ ! -L "${current_link}" ]; then
  echo "模型准入 Worker 当前版本路径不是符号链接，拒绝覆盖"
  exit 1
fi
if [ "${management_enabled}" != "true" ] || [ "${worker_enabled}" != "true" ]; then
  systemctl --user disable --now fusion-litellm-model-management.timer >/dev/null 2>&1 || true
  echo "手动回滚目标未启用模型准入 Worker；定时器保持关闭"
  exit 0
fi
if [ ! -d "${target_release}" ] || [ ! -f "${target_service_unit}" ] || [ ! -f "${target_timer_unit}" ]; then
  systemctl --user disable --now fusion-litellm-model-management.timer >/dev/null 2>&1 || true
  echo "手动回滚目标没有完整配套的模型准入 Worker 资产；已安全关闭定时器"
  exit 0
fi
install -d -m 0755 "${unit_dir}"
install -m 0644 "${target_service_unit}" "${target_timer_unit}" "${unit_dir}/"
ln -sfn "${target_release}" "${current_link}"
systemctl --user daemon-reload
systemd-analyze --user verify \
  "${unit_dir}/fusion-litellm-model-management.service" \
  "${unit_dir}/fusion-litellm-model-management.timer"
systemctl --user enable --now fusion-litellm-model-management.timer
systemctl --user is-active --quiet fusion-litellm-model-management.timer
