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
cd "${FUSION_API_RUNTIME_DIR}"
set -a
source "${FUSION_RUNTIME_ENV}"
set +a
docker run --rm \
  --network postgres_default \
  -e DATABASE_URL="${DATABASE_URL}" \
  "${DEPLOY_API_IMAGE}" \
  alembic upgrade head
