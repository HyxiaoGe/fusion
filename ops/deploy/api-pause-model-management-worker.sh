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
management_enabled="$(printf '%s' "${DEPLOY_LITELLM_MODEL_MANAGEMENT_ENABLED}" | tr '[:upper:]' '[:lower:]')"
worker_enabled="$(printf '%s' "${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" | tr '[:upper:]' '[:lower:]')"
case "${management_enabled}:${worker_enabled}" in
  true:true|true:false|false:false) ;;
  *) echo "模型管理开关必须为 true 或 false，且 Worker 不能单独启用"; exit 1 ;;
esac
systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-model-management.service; then
  if [ "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ACTIVE}" = "true" ]; then
    systemctl --user start fusion-litellm-model-management.timer
    systemctl --user is-active --quiet fusion-litellm-model-management.timer
  fi
  echo "模型准入 Worker 仍在执行，拒绝部署 API；定时器已恢复部署前状态"
  exit 1
fi
