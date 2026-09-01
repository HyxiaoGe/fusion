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
if ! timeout 15s bash ~/scripts/push-cicd-metrics.sh \
  fusion-ui \
  "${METRICS_REF_NAME}" \
  dev \
  "${METRICS_JOB_STATUS}" \
  "${METRICS_STARTED_AT}" \
  "${METRICS_IMAGE_REF}" \
  "${METRICS_TARGET_SHA}" \
  "${METRICS_RUNNER_NAME}" \
  fusion-ui \
  "${METRICS_DEPLOY_START}"; then
  echo "CI/CD metrics push failed, ignored"
fi
