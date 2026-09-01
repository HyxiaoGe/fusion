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
rollbackSha="$ROLLBACK_SHA"
rollbackReason="$ROLLBACK_REASON"

if [ -n "$rollbackSha" ]; then
  if ! [[ "$rollbackSha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "rollback_sha 必须是 40 位小写十六进制提交 SHA"
    exit 1
  fi
  if [ -z "$rollbackReason" ] || ! [[ "$rollbackReason" =~ [^[:space:]] ]]; then
    echo "设置 rollback_sha 时必须填写 rollback_reason"
    exit 1
  fi
elif [ -n "$rollbackReason" ]; then
  echo "仅填写 rollback_reason 无效，必须同时设置 rollback_sha"
  exit 1
fi

if ! [[ "$DEPLOY_TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "实际部署目标不是合法的 40 位小写十六进制提交 SHA"
  exit 1
fi

echo "部署目标校验通过：${DEPLOY_TARGET_SHA}"
