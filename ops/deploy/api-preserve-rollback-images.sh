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
docker image inspect "${ROLLBACK_API_IMAGE_REF}" >/dev/null
docker image inspect "${ROLLBACK_ADAPTER_IMAGE_REF}" >/dev/null
echo "Task 2 保留切换前镜像，待 Task 5 再清理"
