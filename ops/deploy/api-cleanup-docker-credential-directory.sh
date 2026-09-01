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
rollback_knowledge_config_file="${RUNNER_TEMP}/fusion-knowledge-rollback-${GITHUB_RUN_ID}-${GITHUB_JOB}-${GITHUB_RUN_ATTEMPT}.env"
case "${rollback_knowledge_config_file}" in
  "${RUNNER_TEMP}"/fusion-knowledge-rollback-*.env) ;;
  *) echo "知识库回滚配置未落在 RUNNER_TEMP 的受管路径中，拒绝删除"; exit 1 ;;
esac
rm -f -- "${rollback_knowledge_config_file}"
expected="${RUNNER_TEMP}/.docker-${GITHUB_RUN_ID}-${GITHUB_JOB}"
if [ -z "${DOCKER_CONFIG:-}" ] || [ "${DOCKER_CONFIG}" != "${expected}" ]; then
  echo "DOCKER_CONFIG 与当前 job 的隔离目录不一致，拒绝删除: ${DOCKER_CONFIG:-<empty>}"
  exit 1
fi
case "${DOCKER_CONFIG}" in
  "${RUNNER_TEMP}"/.docker-*) ;;
  *) echo "DOCKER_CONFIG 未落在 RUNNER_TEMP 的 .docker-* 路径中，拒绝删除"; exit 1 ;;
esac
rm -rf -- "${DOCKER_CONFIG}"
