#!/usr/bin/env bash
set -eo pipefail

if [ "${OPS_DEPLOY_DRY_RUN:-false}" = "true" ]; then
  if [ "${GITHUB_ACTIONS:-false}" = "true" ]; then
    printf 'GitHub Actions 不允许部署脚本 dry-run\n' >&2
    exit 1
  fi
  printf 'DRY-RUN %s\n' "${BASH_SOURCE[0]##*/}"
  exit 0
fi

# OPS_DEPLOY_BODY_BEGIN
preflight="${GITHUB_WORKSPACE}/ops/deploy/prompt-hold-preflight.py"
if [ "${1}" = "running" ]; then
  docker exec -i fusion-api python - < "${preflight}"
else
  case "${1}" in
    *@sha256:*|sha256:*) ;;
    *) printf 'Prompt hold 目标检查必须使用不可变镜像身份\n' >&2; exit 1 ;;
  esac
  # 调用方已经解析最终运行配置；不重读 env-file，也不传递外部服务凭据。
  docker run --rm -i --network postgres_default --entrypoint python \
    -e DATABASE_URL -e PROMPTHUB_PROJECT_SLUG -e PROMPTHUB_SYNC_MODE -e PROMPT_P0_BASELINE_ATTESTED \
    "${1}" - < "${preflight}"
fi
