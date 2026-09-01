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
image_id="$(docker inspect fusion-ui --format '{{.Image}}')"
python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" record \
  --path "${FUSION_UI_LEDGER}" --app ui --sha "${DEPLOY_TARGET_SHA}" \
  --run-id "${GITHUB_RUN_ID}" --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --image "ui|${DEPLOY_UI_IMAGE}|${image_id}"
