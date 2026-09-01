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
# scripts/deployment_smoke.py checks /health and /api/models/.
if ! python3 "${GITHUB_WORKSPACE}/backend/scripts/deployment_smoke.py" --base-url http://127.0.0.1:8002; then
  echo "fusion-api deployment smoke failed"
  docker logs --tail=120 fusion-api || true
  exit 1
fi
