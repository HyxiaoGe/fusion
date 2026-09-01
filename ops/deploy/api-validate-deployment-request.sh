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
rollback_sha="${REQUESTED_ROLLBACK_SHA:-}"
rollback_reason="${REQUESTED_ROLLBACK_REASON:-}"
target_sha="${REQUESTED_DEPLOY_SHA}"
rollback_requested="false"

if [[ ! "${target_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "deploy_sha 必须是 40 位小写 Git SHA"
  exit 1
fi

if [ "${EVENT_NAME}" = "workflow_dispatch" ]; then
  if [ -n "${rollback_sha}" ]; then
    if [[ ! "${rollback_sha}" =~ ^[0-9a-f]{40}$ ]]; then
      echo "rollback_sha 必须是 40 位小写 Git SHA"
      exit 1
    fi
    if [[ ! "${rollback_reason}" =~ [^[:space:]] ]]; then
      echo "填写 rollback_sha 时 rollback_reason 必填"
      exit 1
    fi
    target_sha="${rollback_sha}"
    rollback_requested="true"
  elif [[ "${rollback_reason}" =~ [^[:space:]] ]]; then
    echo "rollback_reason 不能在 rollback_sha 为空时单独填写"
    exit 1
  fi
elif [ -n "${rollback_sha}" ] || [[ "${rollback_reason}" =~ [^[:space:]] ]]; then
  echo "push 事件不得携带手动回滚参数"
  exit 1
fi

{
  printf '%s\n' "started_at=$(date +%s)"
  printf '%s\n' "target_sha=${target_sha}"
  printf '%s\n' "rollback_requested=${rollback_requested}"
} >> "${GITHUB_OUTPUT}"
