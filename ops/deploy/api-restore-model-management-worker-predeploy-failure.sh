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
if [ "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ACTIVE}" = "true" ]; then
  systemctl --user start fusion-litellm-model-management.timer
  systemctl --user is-active --quiet fusion-litellm-model-management.timer
else
  systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
fi
