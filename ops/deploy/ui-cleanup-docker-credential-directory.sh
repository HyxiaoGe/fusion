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
dockerConfig="${DOCKER_CONFIG:-}"
if [ -z "$dockerConfig" ]; then
  echo "DOCKER_CONFIG 未设置，无需清理"
  exit 0
fi

expectedDockerConfig="${RUNNER_TEMP}/.docker-${GITHUB_RUN_ID}-${GITHUB_JOB}"
if [[ "$dockerConfig" != "$expectedDockerConfig" || "$dockerConfig" != "${RUNNER_TEMP}/.docker-"* ]]; then
  echo "拒绝清理非本次 job 的 Docker 配置目录: $dockerConfig"
  exit 1
fi

rm -rf -- "$dockerConfig"
