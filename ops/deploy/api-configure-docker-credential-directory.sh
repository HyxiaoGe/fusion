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
docker_config="${RUNNER_TEMP}/.docker-${GITHUB_RUN_ID}-${GITHUB_JOB}"
case "${docker_config}" in
  "${RUNNER_TEMP}"/.docker-*) ;;
  *) echo "Docker 凭据目录未落在 RUNNER_TEMP 的隔离路径中"; exit 1 ;;
esac
mkdir -p -- "${docker_config}"
printf '%s\n' "DOCKER_CONFIG=${docker_config}" >> "${GITHUB_ENV}"
