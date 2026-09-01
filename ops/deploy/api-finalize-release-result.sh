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
if [ "${PREPARE_RESULT}" != "success" ]; then
  echo "部署请求校验失败或未执行: ${PREPARE_RESULT}"
  exit 1
fi

if [ "${ROLLBACK_REQUESTED}" = "true" ]; then
  if [ "${PUBLISH_RESULT}" = "skipped" ] && [ "${DEPLOY_RESULT}" = "success" ]; then
    echo "手动回滚成功，Windows publish 按设计跳过"
    exit 0
  fi
  echo "手动回滚失败: publish=${PUBLISH_RESULT} deploy=${DEPLOY_RESULT}"
  exit 1
fi

if [ "${PUBLISH_RESULT}" = "success" ] && [ "${DEPLOY_RESULT}" = "success" ]; then
  echo "正常发布成功"
  exit 0
fi
echo "正常发布失败: publish=${PUBLISH_RESULT} deploy=${DEPLOY_RESULT}"
exit 1
